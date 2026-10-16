import importlib

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI

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
        command.current(Config(str(ROOT / "alembic.ini")))
