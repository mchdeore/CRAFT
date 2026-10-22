from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from typing import cast
from uuid import uuid4

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import WatchError
from rq import Queue, SimpleWorker, Worker
from rq.job import Job, JobStatus
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import Conflict, Forbidden, NotFound, NotSignedIn
from app.library import enrichment, queue
from app.library.handlers import (
    IndexJob,
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


def only_queued_job_id(settings):
    connection = queue.open_connection(settings)
    try:
        identifiers = queue.open_queue(settings, connection).job_ids
        assert len(identifiers) == 1
        return identifiers[0]
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
    calls: list[tuple[bool, bool]] = []

    def fake_index_build(settings, rebuild, *, enrich_ocr=False):
        _ = settings
        calls.append((rebuild, enrich_ocr))
        return {"versions": 3}

    monkeypatch.setattr(queue, "index_build", fake_index_build)
    caller = library["callers"]["admin"]

    queued = enqueue_index_job(caller, IndexJobRequest(rebuild=False, enrich_ocr=True))
    assert queued.status == "queued"
    assert queued.enrich_ocr is True

    run_once(library["settings"])

    status = read_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert status.status == "finished"
    assert status.result == {"versions": 3}
    assert status.enrich_ocr is True
    assert calls == [(False, True)]

    health = read_index_queue(caller)
    assert health.queue == library["settings"].queue_name
    assert health.queued == 0
    assert health.finished >= 1


def test_failed_index_job_reports_error(library, fake_queue, monkeypatch):
    def fail_build(settings, rebuild, *, enrich_ocr=False):
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
    calls: list[tuple[bool, bool]] = []

    def fake_index_build(settings, rebuild, *, enrich_ocr=False):
        _ = settings
        calls.append((rebuild, enrich_ocr))
        return {"versions": 1}

    monkeypatch.setattr(queue, "index_build", fake_index_build)
    caller = library["callers"]["admin"]

    queued = enqueue_index_job(caller, IndexJobRequest(rebuild=True, enrich_ocr=True))
    canceled = cancel_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert canceled.status == "canceled"
    assert canceled.enrich_ocr is True
    connection = queue.open_connection(library["settings"])
    try:
        assert Job.fetch(queued.job_id, connection=connection).get_status() == JobStatus.CANCELED
    finally:
        connection.close()

    retried = retry_index_job(caller, IndexJobRef(job_id=queued.job_id))
    assert retried.status == "queued"
    assert retried.retry_of == queued.job_id
    assert retried.enrich_ocr is True

    run_once(library["settings"])

    done = read_index_job(caller, IndexJobRef(job_id=retried.job_id))
    assert done.status == "finished"
    assert done.result == {"versions": 1}
    assert done.enrich_ocr is True
    assert calls == [(True, True)]


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


def test_ocr_job_enqueue_deduplicates_and_executes(library, fake_queue, monkeypatch):
    _ = fake_queue
    with library["sessions"].begin() as session:
        item, version = session.execute(
            select(m.doc_versions.c.item, m.doc_versions.c.version).limit(1)
        ).one()
    calls: list[tuple[str, int]] = []

    def fake_ocr_version(settings, job_item: str, job_version: int):
        _ = settings
        calls.append((job_item, job_version))
        return {
            "item": job_item,
            "version": job_version,
            "model": "synthetic-ocr",
            "page_count": 1,
            "characters": 42,
        }

    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: None)
    monkeypatch.setattr(enrichment, "ocr_version", fake_ocr_version)
    scheduled = enrichment.enqueue_version_jobs(
        library["settings"], [(item, version), (item, version)]
    )
    assert scheduled == 1
    job_id = only_queued_job_id(library["settings"])

    run_once(library["settings"])

    connection = queue.open_connection(library["settings"])
    try:
        managed = queue.open_queue(library["settings"], connection)
        job = managed.fetch_job(job_id)
        assert job is not None
        assert queue.status_of(job) == "finished"
        latest = job.latest_result(timeout=0)
        assert latest is not None
        assert isinstance(latest.return_value, dict)
        assert latest.return_value["status"] == "updated"
    finally:
        connection.close()

    assert calls == [(item, version)]
    # A missing persisted result recovers only after the durable cooldown.
    assert enrichment.enqueue_version_jobs(library["settings"], [(item, version)]) == 0
    connection = queue.open_connection(library["settings"])
    try:
        connection.hset(
            f"craft:ocr:{library['settings'].queue_name}:{item}:{version}:policy",
            "retry_after",
            "0",
        )
    finally:
        connection.close()
    assert enrichment.enqueue_version_jobs(library["settings"], [(item, version)]) == 1
    run_once(library["settings"])
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: "Generated persisted OCR")
    assert enrichment.enqueue_version_jobs(library["settings"], [(item, version)]) == 0


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


def test_ocr_retry_preserves_job_type_arguments_and_response_schema(
    library, fake_queue, monkeypatch
):
    import msgspec

    with library["sessions"].begin() as session:
        item, version = session.execute(
            select(m.doc_versions.c.item, m.doc_versions.c.version).limit(1)
        ).one()
    monkeypatch.setattr(queue.Settings, "from_env", lambda: library["settings"])
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: "Existing generated OCR")
    calls = []

    def extract(settings, requested_item, requested_version):
        calls.append((requested_item, requested_version))
        return {"item": requested_item, "version": requested_version, "model": "synthetic-ocr"}

    monkeypatch.setattr(enrichment, "ocr_version", extract)
    enrichment.enqueue_version_jobs(library["settings"], [(item, version)], force=True)
    caller = library["callers"]["admin"]
    original = IndexJobRef(job_id=only_queued_job_id(library["settings"]))
    cancel_index_job(caller, original)
    retried = retry_index_job(caller, original)
    assert (retried.kind, retried.item, retried.version, retried.force) == (
        "ocr",
        item,
        version,
        True,
    )
    assert retried.retry_of == original.job_id
    run_once(library["settings"])
    result = read_index_job(caller, IndexJobRef(job_id=retried.job_id))
    assert result.status == "finished"
    assert result.result == {
        "item": item,
        "version": version,
        "model": "synthetic-ocr",
        "status": "updated",
    }
    assert calls == [(item, version)]
    assert msgspec.json.decode(msgspec.json.encode(result), type=IndexJob) == result


def test_ocr_retry_and_reenqueue_share_active_attempt(settings, fake_queue, monkeypatch):
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: None)
    item = str(uuid4())
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 1
    original = IndexJobRef(job_id=only_queued_job_id(settings))
    cancel_index_job(caller, original)
    retried = retry_index_job(caller, original)
    assert retried.job_id != original.job_id
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 0
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)], force=True) == 0
    assert retry_index_job(caller, original).job_id == retried.job_id
    assert read_index_job(caller, original).status == "canceled"
    connection = queue.open_connection(settings)
    try:
        assert queue.open_queue(settings, connection).job_ids == [retried.job_id]
    finally:
        connection.close()


def test_concurrent_ocr_enqueue_claim_is_atomic(settings, fake_queue, monkeypatch):
    barrier = Barrier(2)
    seen = local()

    def missing(*_):
        if not getattr(seen, "called", False):
            seen.called = True
            barrier.wait(timeout=10)
        return None

    monkeypatch.setattr(enrichment, "_read_ocr_text", missing)
    item = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled = list(
            pool.map(lambda _: enrichment.enqueue_version_jobs(settings, [(item, 1)]), range(2))
        )
    assert sorted(scheduled) == [0, 1]
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        assert len(managed.job_ids) == 1
        job = managed.fetch_job(managed.job_ids[0])
        assert job is not None
        assert (job.kwargs["item"], job.kwargs["version"]) == (item, 1)
    finally:
        connection.close()


@pytest.mark.parametrize("names", [("ocr-a", "ocr-b"), ("ocr:a", "ocr-a")])
def test_ocr_job_identity_is_isolated_between_queues(settings, fake_queue, names):
    connection = queue.open_connection(settings)
    try:
        first_queue, second_queue = (Queue(name, connection=connection) for name in names)
        item = str(uuid4())
        first, first_added = enrichment.enqueue_version_job(
            first_queue, settings, item, 1, force=True
        )
        second, second_added = enrichment.enqueue_version_job(
            second_queue, settings, item, 1, force=True
        )
        assert first_added and second_added
        assert first is not None and second is not None
        assert first.id != second.id
        assert first_queue.job_ids == [first.id]
        assert second_queue.job_ids == [second.id]
        for managed, original in ((first_queue, first), (second_queue, second)):
            persisted = managed.fetch_job(original.id)
            assert persisted is not None and persisted.origin == managed.name
            repeated, added = enrichment.enqueue_version_job(managed, settings, item, 1, force=True)
            assert repeated is not None and repeated.id == original.id
            assert not added
    finally:
        connection.close()


@pytest.mark.parametrize("mismatch", [None, "queue", "function", "item", "version"])
def test_ocr_legacy_job_is_reused_only_when_compatible(settings, fake_queue, mismatch):
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        legacy_queue = (
            Queue("legacy-other", connection=connection) if mismatch == "queue" else managed
        )
        item = str(uuid4())
        function = queue.run_index_job if mismatch == "function" else enrichment.run_ocr_job
        details = {
            "item": str(uuid4()) if mismatch == "item" else item,
            "version": 2 if mismatch == "version" else 1,
        }
        legacy = legacy_queue.enqueue_call(function, kwargs=details, job_id=f"library_ocr_{item}_1")
        job, added = enrichment.enqueue_version_job(managed, settings, item, 1, force=True)
        assert job is not None
        assert added == (mismatch is not None)
        assert (job.id == legacy.id) == (mismatch is None)
        persisted = legacy_queue.fetch_job(legacy.id)
        assert persisted is not None and persisted.kwargs == details
    finally:
        connection.close()


def test_persisted_ocr_skips_enqueue_after_job_record_expires(settings, fake_queue, monkeypatch):
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: "Generated retained OCR")
    assert enrichment.enqueue_version_jobs(settings, [(str(uuid4()), 1)]) == 0


def test_persisted_ocr_expires_legacy_claim_and_policy(settings, fake_queue, monkeypatch):
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: "Generated retained OCR")
    item = str(uuid4())
    identity = f"craft:ocr:{settings.queue_name}:{item}:1"
    connection = queue.open_connection(settings)
    try:
        connection.set(identity, "expired-generated-job")
        connection.hset(identity + ":policy", "attempts", "1")
        managed = queue.open_queue(settings, connection)
        assert enrichment.enqueue_version_job(managed, settings, item, 1) == (None, False)
        for key in (identity, identity + ":policy"):
            assert 0 < cast(int, connection.ttl(key)) <= enrichment.TERMINAL_TTL
    finally:
        connection.close()


def test_active_ocr_claim_persists_during_repeated_enqueue(settings, fake_queue):
    item = str(uuid4())
    identity = f"craft:ocr:{settings.queue_name}:{item}:1"
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        first, added = enrichment.enqueue_version_job(managed, settings, item, 1, force=True)
        assert first is not None and added
        for key in (identity, identity + ":policy"):
            connection.expire(key, 60)
        repeated, added = enrichment.enqueue_version_job(managed, settings, item, 1, force=True)
        assert repeated is not None and repeated.id == first.id and not added
        for key in (identity, identity + ":policy"):
            assert connection.ttl(key) == -1
    finally:
        connection.close()


def test_terminal_callback_only_expires_its_own_claim(settings, fake_queue):
    item = str(uuid4())
    identity = f"craft:ocr:{settings.queue_name}:{item}:1"
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        job, _ = enrichment.enqueue_version_job(managed, settings, item, 1, force=True)
        assert job is not None
        connection.set(identity, "new-generated-active-job")
        enrichment.expire_terminal_claim(job, connection)
        for key in (identity, identity + ":policy"):
            assert connection.ttl(key) == -1
        connection.set(identity, job.id)
        enrichment.expire_terminal_claim(job, connection)
        for key in (identity, identity + ":policy"):
            assert 0 < cast(int, connection.ttl(key)) <= enrichment.TERMINAL_TTL
    finally:
        connection.close()


def test_excess_execution_records_fail_closed(settings, fake_queue):
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        job = managed.enqueue(queue.run_index_job)
        connection.zadd(job.execution_registry.key, {f"generated-{i}": i for i in range(33)})
        with pytest.raises(queue.QueueUnavailable):
            queue.status_of(job)
    finally:
        connection.close()


def test_permanent_ocr_failure_requires_explicit_retry(settings, fake_queue, monkeypatch):
    monkeypatch.setattr(enrichment.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: None)

    def rejected(*args):
        raise enrichment.OcrPermanentError("Generated permanent rejection.")

    monkeypatch.setattr(enrichment, "ocr_version", rejected)
    item = str(uuid4())
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 1
    original = IndexJobRef(job_id=only_queued_job_id(settings))
    run_once(settings)
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 0
    connection = queue.open_connection(settings)
    try:
        assert (
            connection.hget(f"craft:ocr:{settings.queue_name}:{item}:1:policy", "permanent") == b"1"
        )
    finally:
        connection.close()
    caller = Caller(user_id=uuid4(), roles=frozenset({"admin"}), settings=settings)
    retried = retry_index_job(caller, original)
    assert retried.job_id != original.job_id
    assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 0


def test_transient_ocr_failures_back_off_and_remain_bounded_after_job_expiry(
    settings, fake_queue, monkeypatch
):
    import time

    import msgspec

    from app.infrastructure.errors import ApiError

    settings = msgspec.structs.replace(settings, ocr_attempt_limit=2, ocr_retry_seconds=20)
    monkeypatch.setattr(enrichment.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: None)

    def unavailable(*args):
        raise ApiError("Generated transient failure.")

    monkeypatch.setattr(enrichment, "ocr_version", unavailable)
    item = str(uuid4())
    policy = f"craft:ocr:{settings.queue_name}:{item}:1:policy"
    connection = queue.open_connection(settings)
    try:
        for attempt in (1, 2):
            assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 1
            job_id = only_queued_job_id(settings)
            run_once(settings)
            recorded = cast(bytes | None, connection.hget(policy, "attempts"))
            retry_after = cast(bytes | None, connection.hget(policy, "retry_after"))
            assert recorded is not None and int(recorded) == attempt
            assert retry_after is not None and float(retry_after) > time.time()
            assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 0
            connection.hset(policy, "retry_after", "0")
        managed = queue.open_queue(settings, connection)
        job = managed.fetch_job(job_id)
        assert job is not None
        job.delete()
        assert enrichment.enqueue_version_jobs(settings, [(item, 1)]) == 0
        assert enrichment.enqueue_version_jobs(settings, [(item, 1)], force=True) == 1
    finally:
        connection.close()
