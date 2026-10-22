import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from app.infrastructure.provider.locking import provider_lock
from app.infrastructure.provider.simulated import SimulatedProvider


def _write(root: str, number: int) -> None:
    SimulatedProvider(root).write_content(f"file-{number}.md", f"Synthetic {number}".encode())


def _assert_records(root: Path) -> None:
    provider = SimulatedProvider(root)
    records = provider.items()
    assert len(records) == 12
    assert all(len(record["versions"]) == 1 for record in records.values())
    changes, cursor = provider.list_changes(0)
    assert [change["cursor"] for change in changes] == list(range(1, 13))
    assert cursor == 12
    assert provider.scan() == []


def test_provider_mutations_are_thread_safe_across_instances(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_write, [str(tmp_path)] * 12, range(12)))
    _assert_records(tmp_path)


@pytest.mark.parametrize("start_method", ["spawn", "fork"])
def test_provider_mutations_are_process_safe(tmp_path, start_method):
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context(start_method)
    ) as pool:
        list(pool.map(_write, [str(tmp_path)] * 12, range(12)))
    _assert_records(tmp_path)


def _feed_after_fork(root, started, finished):
    started.set()
    SimulatedProvider(root).feed("itm_000000000001", "changed")
    finished.set()


def test_forked_child_does_not_unlock_parent(tmp_path):
    context = multiprocessing.get_context("fork")
    started, finished = context.Event(), context.Event()
    with provider_lock(tmp_path / ".provider"):
        child = context.Process(target=_feed_after_fork, args=(str(tmp_path), started, finished))
        child.start()
        assert started.wait(10)
        assert not finished.wait(0.2)
    try:
        assert finished.wait(10)
    finally:
        child.join(10)
    assert child.exitcode == 0


def test_read_only_provider_access_does_not_create_metadata(tmp_path):
    provider = SimulatedProvider(tmp_path)
    assert provider.items() == {}
    assert provider.list_changes(0) == ([], 0)
    assert not provider.meta.exists()
