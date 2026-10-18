"""Capability discovery must describe the actual agent transport allowlist."""

import asyncio
import inspect
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.errors import NotSignedIn
from app.infrastructure.operations.registry import registry, tool_operations
from app.tools import handlers


def catalog(caller: Caller) -> handlers.CapabilityCatalog:
    operation = getattr(handlers, "list_capabilities", None)
    assert callable(operation), "Missing authenticated list_capabilities operation"
    result = operation(caller)
    if inspect.isawaitable(result):
        result = asyncio.run(cast(Coroutine[Any, Any, handlers.CapabilityCatalog], result))
    return cast(handlers.CapabilityCatalog, result)


def test_capabilities_match_actual_agent_allowlist():
    result = catalog(Caller(user_id=uuid4()))
    expected = sorted(op.tool_name for op in tool_operations(registry, agent=True))
    assert [tool.name for tool in result.tools] == expected
    assert result.skills == []
    assert result.skills_status == "not_installed"
    assert "ask_agent" not in expected
    for tool in result.tools:
        operation = next(op for op in registry.values() if op.tool_name == tool.name)
        description = inspect.getdoc(operation.function)
        assert description is not None
        assert tool.description and tool.description in description
        assert len(tool.description) <= 240 and "\n" not in tool.description
    assert set(msgspec.to_builtins(result)) == {"tools", "skills", "skillsStatus"}


def test_capabilities_follow_registry_changes_and_effect_filter(monkeypatch):
    def generated_probe(caller: Caller) -> None:
        """Inspect generated probe metadata safely."""

    original = next(op for op in tool_operations(registry, agent=True))
    probe = replace(
        original,
        name="probe.generated_catalog_probe",
        function=generated_probe,
        access=SIGNED_IN,
        input=None,
        output=type(None),
        effects="NONE",
    )
    monkeypatch.setitem(registry, probe.name, probe)
    effect = replace(probe, name="probe.generated_effect_probe", effects="DATA")
    monkeypatch.setitem(registry, effect.name, effect)
    private = replace(probe, name="probe.generated_private_probe", tool=False)
    monkeypatch.setitem(registry, private.name, private)
    result = catalog(Caller(user_id=uuid4()))
    names = [tool.name for tool in result.tools]
    assert "generated_catalog_probe" in names
    assert not {"generated_effect_probe", "generated_private_probe"}.intersection(names)
    assert next(tool.description for tool in result.tools if tool.name == probe.tool_name) == (
        "Inspect generated probe metadata safely."
    )


def test_capabilities_require_signed_in_caller():
    with pytest.raises(NotSignedIn):
        catalog(Caller())


def test_capabilities_http_requires_authentication_and_returns_same_catalog(chat, chart_http):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as anonymous:
        assert anonymous.get("/api/tools/capabilities").status_code == 401
    response = chart_http.get("/api/tools/capabilities")
    assert response.status_code == 200
    assert response.json() == msgspec.to_builtins(catalog(chat["caller"]))
