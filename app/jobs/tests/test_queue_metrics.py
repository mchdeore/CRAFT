"""Queue samples, job_metrics and craft queues report with generated jobs only."""

import json
import time

import msgspec
from sqlalchemy import text

from app.cli import main, parse_since
from app.infrastructure import queue
from app.infrastructure.database.engine import make_engine, make_sessions
from app.jobs import service, storage
from app.jobs.tests.test_jobs import jobs as jobs
from app.tests.support import jobs as test_jobs


def test_parse_since_durations():
    assert parse_since("30s") == 30
    assert parse_since("24h") == 86400
    assert parse_since("7d") == 7 * 86400


def test_samples_and_report_match_known_jobs(settings, jobs, owner_engine, monkeypatch, capsys):
    monkeypatch.setattr(queue, "SAMPLE_SECONDS", 0.05)
    monkeypatch.setattr(queue, "RECOVER_SECONDS", 0.05)
    with queue.worker_thread(settings, ["interactive"], concurrency=2):
        done = [jobs.defer(test_jobs.sleep, seconds=0) for _ in range(3)]
        for job in done:
            assert jobs.wait(job, {"finished", "failed"}).state == "finished"
        # Wait long enough for the maintenance loop to write at least one sample.
        time.sleep(0.3)

    with owner_engine.connect() as connection:
        metrics = connection.execute(
            text(
                "select count(*) from job_metrics"
                " where kind = 'tests.sleep' and outcome = 'finished'"
                "   and wait_seconds is not null and run_seconds is not null"
            )
        ).scalar_one()
        samples = connection.execute(
            text("select count(*) from queue_samples where queue = 'interactive'")
        ).scalar_one()
    assert metrics >= 3
    assert samples >= 1

    engine = make_engine(settings)
    try:
        sessions = make_sessions(engine)
        report = service.report(sessions, since_seconds=3600)
        health = service.health(sessions, since_seconds=3600)
    finally:
        engine.dispose()

    interactive = next(row for row in report["queues"] if row["queue"] == "interactive")
    kind = interactive["kinds"]["tests.sleep"]
    assert kind["jobs"] >= 3
    assert kind["outcomes"].get("finished", 0) >= 3
    assert kind["wait_p50_seconds"] is not None
    assert kind["run_p50_seconds"] is not None
    assert interactive["peak_queued"] >= 0

    health_row = next(row for row in health["queues"] if row["queue"] == "interactive")
    assert health_row["wait_p50_seconds"] is not None
    assert health_row["run_p50_seconds"] is not None

    # Direct sample insert proves saturation counting for a known window.
    monkeypatch.setattr(queue, "SAMPLE_SECONDS", 30.0)
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "insert into queue_samples"
                " (queue, queued, running, workers_alive, concurrency, oldest_wait_seconds)"
                " values ('interactive', 5, 2, 1, 2, 1.5)"
            )
        )
    engine = make_engine(settings)
    try:
        report = service.report(make_sessions(engine), since_seconds=3600)
    finally:
        engine.dispose()
    interactive = next(row for row in report["queues"] if row["queue"] == "interactive")
    assert interactive["peak_queued"] >= 5
    assert interactive["saturated_seconds"] >= 30

    monkeypatch.setattr(
        "app.infrastructure.config.settings.Settings.from_env", lambda env=None: settings
    )
    main(["queues", "report", "--since", "1h"])
    printed = json.loads(capsys.readouterr().out)
    assert printed["since_seconds"] == 3600
    assert {row["queue"] for row in printed["queues"]} >= {"agent", "interactive", "bulk"}


def test_sample_queues_writes_row(settings, owner_engine):
    engine = make_engine(settings)
    try:
        with make_sessions(engine).begin() as session:
            storage.sample_queues(session, ["bulk"], concurrency=50, stalled=30.0)
    finally:
        engine.dispose()
    with owner_engine.connect() as connection:
        row = connection.execute(
            text(
                "select queued, running, concurrency from queue_samples"
                " where queue = 'bulk' order by id desc limit 1"
            )
        ).one()
    assert row.concurrency == 50
    assert row.queued >= 0 and row.running >= 0


def test_concurrency_comes_from_per_queue_settings(settings):
    assert settings.concurrency_for(["agent"]) == settings.worker_concurrency_agent
    assert settings.concurrency_for(["interactive"]) == settings.worker_concurrency_interactive
    # One worker taking a small queue beside a large one keeps the large one's number.
    assert settings.concurrency_for(["agent", "bulk"]) == max(
        settings.worker_concurrency_agent, settings.worker_concurrency_bulk
    )
    sized = msgspec.structs.replace(
        settings, worker_concurrency_agent=20, worker_concurrency_bulk=2
    )
    assert sized.concurrency_for(["agent", "bulk"]) == 20
    assert sized.concurrency_for(["bulk"]) == 2
