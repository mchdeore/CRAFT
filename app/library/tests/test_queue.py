from __future__ import annotations

from uuid import uuid4

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import WatchError
from rq import Queue, SimpleWorker, Worker
from rq.job import Job, JobStatus

from app.infrastructure.access import Caller
from app.infrastructure.errors import Conflict, Forbidden, NotFound, NotSignedIn
from app.library import queue
from app.library.handlers import (
    IndexJobPageRequest,
    IndexJobRef,
    IndexJobRequest,
    cancel_index_job,
    enqueue_index_job,
    list_index_jobs,
    read_index_job,
    read_index_queue,
    retry_index_job,
)


def run_once(settings):
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        worker = SimpleWorker([managed], connection=connection)
        worker.work(burst=True, with_scheduler=False)
    finally:
        connection.close()


def test_queue_health_preserves_bounded_api_timeouts(settings, monkeypatch):
    server = fakeredis.FakeServer()
    connections = []
    connection_options = []

    def connect(url, **kwargs):
        connection_options.append(kwargs)
        connection = fakeredis.FakeRedis(server=server, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(queue.Redis, "from_url", connect)
    connection = queue.open_connection(settings)
    worker = SimpleWorker([queue.open_queue(settings, connection)], connection=connection)
    worker.register_birth()
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    health = queue.queue_health(caller)
    assert [entry["name"] for entry in health["workers"]] == [worker.name]
    options = connections[-1].connection_pool.connection_kwargs
    assert options["socket_timeout"] == 5
    assert connection_options[-1]["socket_connect_timeout"] == 5


def test_worker_read_timeout_allows_blocking_dequeue(settings):
    connection = queue.open_connection(settings)
    try:
        worker = Worker(
            [queue.open_queue(settings, connection)],
            connection=connection,
            prepare_for_work=False,
        )
        options = connection.connection_pool.connection_kwargs
        assert options["socket_connect_timeout"] == 5
        assert options["socket_timeout"] >= worker.connection_timeout > worker.dequeue_timeout
    finally:
        connection.close()


def test_cancel_contention_is_bounded_and_preserves_queued_job(settings, fake_queue, monkeypatch):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    queued = enqueue_index_job(caller, IndexJobRequest())
    original_pipeline = fakeredis.FakeRedis.pipeline
    attempts = 0

    def pipeline(*args, **kwargs):
        result = original_pipeline(*args, **kwargs)

        def contend(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise WatchError("generated contention")

        monkeypatch.setattr(result, "execute", contend)
        return result

    monkeypatch.setattr(fakeredis.FakeRedis, "pipeline", pipeline)
    with pytest.raises(Conflict, match="retry cancellation"):
        cancel_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert 0 < attempts <= 8
    assert read_index_job(caller, IndexJobRef(job_id=queued.job_id)).status == "queued"


def test_index_job_enqueue_execute_and_observe(library, fake_queue, monkeypatch):
    _ = fake_queue
    monkeypatch.setattr(queue, "index_build", lambda settings, rebuild: {"versions": 3})
    caller = library["callers"]["admin"]

    queued = enqueue_index_job(caller, IndexJobRequest(rebuild=False))
    assert queued.status == "queued"

    run_once(library["settings"])

    status = read_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert status.status == "finished"
    assert status.result == {"versions": 3}

    health = read_index_queue(caller)
    assert health.queue == library["settings"].queue_name
    assert health.queued == 0
    assert health.finished >= 1


def test_failed_index_job_reports_error(library, fake_queue, monkeypatch):
    def fail_build(settings, rebuild):
        raise RuntimeError("Generated indexing failure.")

    monkeypatch.setattr(queue, "index_build", fail_build)
    caller = library["callers"]["admin"]
    queued = enqueue_index_job(caller, IndexJobRequest())
    run_once(library["settings"])

    status = read_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert status.status == "failed"
    assert status.error == "RuntimeError: Generated indexing failure."


def test_index_job_cancel_then_retry(library, fake_queue, monkeypatch):
    _ = fake_queue
    monkeypatch.setattr(queue, "index_build", lambda settings, rebuild: {"versions": 1})
    caller = library["callers"]["admin"]

    queued = enqueue_index_job(caller, IndexJobRequest(rebuild=True))
    canceled = cancel_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert canceled.status == "canceled"
    connection = queue.open_connection(library["settings"])
    try:
        assert Job.fetch(queued.job_id, connection=connection).get_status() == JobStatus.CANCELED
    finally:
        connection.close()

    retried = retry_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert retried.status == "queued"
    assert retried.retry_of == queued.job_id

    run_once(library["settings"])

    done = read_index_job(caller, IndexJobRef(job_id=retried.job_id))
    assert done.status == "finished"
    assert done.result == {"versions": 1}


@pytest.mark.parametrize("worker_state", [JobStatus.QUEUED, JobStatus.STARTED])
def test_cancel_does_not_mutate_a_job_claimed_during_cancellation(
    settings, fake_queue, monkeypatch, worker_state
):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    queued = enqueue_index_job(caller, IndexJobRequest())
    connection = queue.open_connection(settings)
    managed = queue.open_queue(settings, connection)
    lpos = fakeredis.FakeRedis.lpos
    claimed = False

    def claim_after_position_read(client, key, value, *args, **kwargs):
        nonlocal claimed
        position = lpos(client, key, value, *args, **kwargs)
        if key == managed.key and position is not None and not claimed:
            claimed = True
            managed.remove(queued.job_id)
            Job.fetch(queued.job_id, connection=connection).set_status(worker_state)
        return position

    monkeypatch.setattr(fakeredis.FakeRedis, "lpos", claim_after_position_read)
    try:
        with pytest.raises(Conflict):
            cancel_index_job(caller, IndexJobRef(job_id=queued.job_id))
        assert claimed
        assert Job.fetch(queued.job_id, connection=connection).get_status() == worker_state
        assert queued.job_id not in managed.canceled_job_registry.get_job_ids()
    finally:
        connection.close()


def test_concurrent_cancellation_remains_idempotent(settings, fake_queue, monkeypatch):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    queued = enqueue_index_job(caller, IndexJobRequest())
    cancel = Job.cancel
    competing_cancel = False

    def cancel_first(job, *args, **kwargs):
        nonlocal competing_cancel
        if not competing_cancel:
            competing_cancel = True
            cancel(Job.fetch(job.id, connection=job.connection))
        return cancel(job, *args, **kwargs)

    monkeypatch.setattr(Job, "cancel", cancel_first)
    canceled = cancel_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert competing_cancel
    assert canceled.status == "canceled"


def test_index_job_retry_requires_terminal_failure_or_cancel(library, fake_queue):
    _ = fake_queue
    caller = library["callers"]["admin"]
    queued = enqueue_index_job(caller, IndexJobRequest())

    with pytest.raises(Conflict):
        retry_index_job(caller, IndexJobRef(job_id=queued.job_id))


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        (enqueue_index_job, IndexJobRequest(rebuild=True)),
        (read_index_job, IndexJobRef(job_id="synthetic-job")),
        (retry_index_job, IndexJobRef(job_id="synthetic-job")),
        (cancel_index_job, IndexJobRef(job_id="synthetic-job")),
        (read_index_queue, None),
        (list_index_jobs, IndexJobPageRequest()),
    ],
)
def test_queue_operations_require_admin(settings, signed_in, operation, payload):
    caller = Caller(user_id=uuid4() if signed_in else None, settings=settings)
    with pytest.raises(Forbidden if signed_in else NotSignedIn):
        operation(caller, payload) if payload is not None else operation(caller)


@pytest.mark.parametrize("started", [False, True])
def test_queued_status_never_enumerates_queue_registries(
    settings, fake_queue, monkeypatch, started
):
    from rq.registry import BaseRegistry

    connection = queue.open_connection(settings)
    managed = queue.open_queue(settings, connection)
    job = managed.enqueue(queue.run_index_job)
    if started:
        connection.zadd(job.execution_registry.key, {"generated-execution": 9_999_999_999})
        connection.zadd(
            managed.started_job_registry.key, {f"{job.id}:generated-execution": 9_999_999_999}
        )

    def forbidden_scan(*args, **kwargs):
        raise AssertionError("API reads must not enumerate or clean queue registries")

    monkeypatch.setattr(BaseRegistry, "get_job_ids", forbidden_scan)
    monkeypatch.setattr(BaseRegistry, "cleanup", forbidden_scan)
    assert queue.status_of(job) == ("started" if started else "queued")
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    queue.queue_health(caller)
    connection.close()


def test_job_pages_bound_scans_skip_foreign_and_missing_jobs(settings, fake_queue, monkeypatch):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    connection = queue.open_connection(settings)
    managed = queue.open_queue(settings, connection)
    foreign = managed.enqueue("builtins.str", "generated")
    accepted = managed.enqueue(queue.run_index_job)
    connection.lpush(managed.key, "missing-generated-job")
    scans = []
    original = Queue.get_job_ids

    def bounded_ids(self, offset=0, length=-1):
        assert length == 3
        scans.append((offset, length))
        return original(self, offset=offset, length=length)

    monkeypatch.setattr(Queue, "get_job_ids", bounded_ids)
    page = list_index_jobs(caller, IndexJobPageRequest(limit=2))
    assert page.jobs == []
    assert page.next_offset == 2
    following = list_index_jobs(caller, IndexJobPageRequest(offset=page.next_offset, limit=2))
    assert [job.job_id for job in following.jobs] == [accepted.id]
    assert following.next_offset is None
    assert scans == [(0, 3), (2, 3)]
    assert foreign.id != accepted.id
    connection.close()


def test_registry_pages_do_not_run_maintenance(settings, fake_queue, monkeypatch):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    connection = queue.open_connection(settings)
    managed = queue.open_queue(settings, connection)
    job = managed.enqueue(queue.run_index_job)
    job.cancel()

    def no_cleanup(*args, **kwargs):
        raise AssertionError("A page read cannot run registry maintenance")

    monkeypatch.setattr(type(managed.canceled_job_registry), "cleanup", no_cleanup)
    page = list_index_jobs(caller, IndexJobPageRequest(status="canceled", limit=1))
    assert [row.job_id for row in page.jobs] == [job.id]
    assert page.next_offset is None
    connection.close()


def test_job_listing_outage_returns_unavailable(settings, fake_queue, monkeypatch):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)

    def unavailable(*args, **kwargs):
        raise RedisConnectionError("synthetic outage")

    monkeypatch.setattr(Queue, "get_job_ids", unavailable)
    with pytest.raises(queue.QueueUnavailable):
        list_index_jobs(caller, IndexJobPageRequest())


@pytest.mark.parametrize("operation", [read_index_job, retry_index_job, cancel_index_job])
def test_job_fetch_outage_returns_unavailable(settings, fake_queue, monkeypatch, operation):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)

    def fail_fetch(*args, **kwargs):
        raise RedisConnectionError("synthetic outage")

    monkeypatch.setattr(queue.Job, "fetch", fail_fetch)
    with pytest.raises(queue.QueueUnavailable) as error:
        operation(caller, IndexJobRef(job_id="synthetic-job"))
    assert error.value.status == 503


@pytest.mark.parametrize("operation", [read_index_job, retry_index_job, cancel_index_job])
@pytest.mark.parametrize("foreign_queue", [True, False])
def test_foreign_job_is_not_exposed(settings, fake_queue, operation, foreign_queue):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    connection = queue.open_connection(settings)
    try:
        managed = Queue(
            "other-jobs" if foreign_queue else settings.queue_name, connection=connection
        )
        foreign = (
            managed.enqueue(queue.run_index_job)
            if foreign_queue
            else managed.enqueue("builtins.str", "generated")
        )
        with pytest.raises(NotFound):
            operation(caller, IndexJobRef(job_id=foreign.id))
    finally:
        connection.close()


@pytest.mark.parametrize("operation", [read_index_job, retry_index_job, cancel_index_job])
def test_job_status_outage_returns_unavailable(settings, fake_queue, monkeypatch, operation):
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    job = enqueue_index_job(caller, IndexJobRequest())

    def fail_status(*args, **kwargs):
        raise RedisConnectionError("synthetic outage")

    monkeypatch.setattr(queue.Job, "get_status", fail_status)
    with pytest.raises(queue.QueueUnavailable):
        operation(caller, IndexJobRef(job_id=job.job_id))


def test_queued_job_indexes_real_generated_document(library, fake_queue, monkeypatch):
    from sqlalchemy import select

    from app.infrastructure.database import metadata as m
    from app.infrastructure.provider.simulated import SimulatedProvider

    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/queued.md", b"# Queued\n\nGenerated worker document.")
    record = next(
        value for value in provider.items().values() if value["path"] == "reference/queued.md"
    )
    monkeypatch.setattr(queue.Settings, "from_env", lambda: library["settings"])
    caller = library["callers"]["admin"]
    job = enqueue_index_job(caller, IndexJobRequest())
    run_once(library["settings"])
    result = read_index_job(caller, IndexJobRef(job_id=job.job_id))
    assert result.status == "finished"
    assert result.result["versions"] == 1
    with library["sessions"].begin() as session:
        assert session.execute(
            select(m.doc_versions.c.id).where(m.doc_versions.c.item == record["item"])
        ).scalar_one()
