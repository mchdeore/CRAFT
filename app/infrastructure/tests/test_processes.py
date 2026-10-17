import sys
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import anyio
from fastapi import FastAPI
from sqlalchemy import Engine

from app import close_on_exit
from app.infrastructure.config.settings import Settings
from craft_processes import ProcessSpec


class MCP:
    @asynccontextmanager
    async def run(self):
        yield


def test_production_lifespan_closes_owned_runtime_and_tool_children(tmp_path):
    engine = MagicMock(spec=Engine)
    app = FastAPI()
    settings = Settings(app_db_password="generated-placeholder", process_root=str(tmp_path))
    roots = []

    async def run():
        async with close_on_exit(engine, MCP(), settings)(app):
            manager = app.state.processes
            for role in ("agent-runtime", "tool-worker"):
                manager.add(
                    ProcessSpec(
                        id=role,
                        role=role,
                        command=(sys.executable, "-c", "import time; time.sleep(60)"),
                    )
                )
            await anyio.to_thread.run_sync(manager.start_all)
            roots.extend([manager.processes.root, manager.processes.ipc])
        assert app.state.processes.closed

    anyio.run(run)
    assert all(not root.exists() for root in roots)
    engine.dispose.assert_called_once()


def test_default_lifespan_does_not_start_daemon():
    engine = MagicMock(spec=Engine)
    app = FastAPI()

    async def run():
        async with close_on_exit(engine, MCP(), Settings(app_db_password="generated-placeholder"))(
            app
        ):
            assert app.state.processes is None

    anyio.run(run)
    engine.dispose.assert_called_once()


def test_cancelled_lifespan_still_closes_owned_daemon(tmp_path):
    engine = MagicMock(spec=Engine)
    app = FastAPI()
    settings = Settings(app_db_password="generated-placeholder", process_root=str(tmp_path))

    async def run():
        with anyio.CancelScope() as cancellation:
            async with close_on_exit(engine, MCP(), settings)(app):
                cancellation.cancel()
                await anyio.sleep(0)
        assert app.state.processes.closed

    anyio.run(run)
    assert list(tmp_path.iterdir()) == []
    engine.dispose.assert_called_once()
