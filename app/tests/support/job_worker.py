"""A worker that also knows the test-only job operations."""

if __name__ == "__main__":
    from app import load_operations
    from app.infrastructure.config.settings import Settings
    from app.infrastructure.queue import run_worker
    from app.tests.support import jobs  # noqa: F401  (registers the test jobs)

    load_operations()
    run_worker(Settings.from_env())
