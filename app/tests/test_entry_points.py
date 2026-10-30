import importlib
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import anyio
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import Engine

from app import close_on_exit
from app.cli import main
from app.infrastructure.paths import ROOT


def test_craft_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.startswith("craft ")


def test_main_builds_the_app_without_a_database_call() -> None:
    assert isinstance(importlib.import_module("app.main").app, FastAPI)


def test_migrations_refuse_to_run_without_the_owner_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRAFT_OWNER_DB_PASSWORD", "")
    with pytest.raises(SystemExit, match="CRAFT_OWNER_DB_PASSWORD"):
        command.current(Config(toml_file=str(ROOT / "pyproject.toml")))


def test_the_command_line_and_craft_id_import_without_the_server() -> None:
    """Every craft command, craft-id and alembic import app, and that registers no operation."""
    script = (
        "import app.cli, app.identity.cli, sys\n"
        "from app.infrastructure.operations.registry import registry\n"
        "heavy = {'openai', 'azure.ai.projects', 'vl_convert'} & set(sys.modules)\n"
        "print(sorted(heavy), len(registry))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.split() == ["[]", "0"]


def test_a_cancelled_lifespan_still_disposes_the_pool() -> None:
    engine = MagicMock(spec=Engine)

    class Transport:
        @asynccontextmanager
        async def run(self):
            yield

    async def run() -> None:
        with anyio.CancelScope() as cancellation:
            async with close_on_exit(engine, Transport())(FastAPI()):
                cancellation.cancel()
                await anyio.sleep(0)

    anyio.run(run)
    engine.dispose.assert_called_once()
