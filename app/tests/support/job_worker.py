"""A worker that also knows the test-only job operations, answering from the scripted model."""

if __name__ == "__main__":
    import pytest

    from app import load_operations
    from app.infrastructure.config.settings import Settings
    from app.infrastructure.queue import run_worker
    from app.tests.support import jobs  # noqa: F401  (registers the test jobs)
    from app.tests.support.model import install

    load_operations()
    with pytest.MonkeyPatch.context() as patch:
        install(patch)
        run_worker(Settings.from_env())
