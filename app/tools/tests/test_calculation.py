import asyncio
from dataclasses import replace

import httpx
import msgspec
import pytest

from app.infrastructure.config.env import read_env
from app.tools.handlers import CalculationInput, check_calculation
from app.tools.service import NotConfigured


def configured(chat, key="synthetic-app-id"):
    return replace(
        chat["caller"], settings=msgspec.structs.replace(chat["settings"], wolfram_app_id=key)
    )


def mock_service(monkeypatch, requests, answer="281011.178 lbf"):
    original = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text=answer)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )


def test_check_agrees_and_disagrees(chat, monkeypatch):
    requests = []
    mock_service(monkeypatch, requests)
    for value, expected in [("281011.178", True), ("100", False)]:
        result = asyncio.run(
            check_calculation(
                configured(chat), CalculationInput(expression="1250 kN to lbf", craft_result=value)
            )
        )
        assert result.agrees is expected and result.checked_at
    assert all(r.url.params["input"] == "1250 kN to lbf" for r in requests)


def test_expression_filter(chat, monkeypatch):
    requests = []
    mock_service(monkeypatch, requests)
    from app.infrastructure.errors import InvalidInput

    for expression in ["Mission identifier plus 4", "2+2 https://example.invalid", "2+2;secret"]:
        with pytest.raises(InvalidInput):
            asyncio.run(
                check_calculation(configured(chat), CalculationInput(expression=expression))
            )
    assert not requests


def test_not_configured(chat, monkeypatch):
    requests = []
    mock_service(monkeypatch, requests)
    with pytest.raises(NotConfigured):
        asyncio.run(check_calculation(configured(chat, ""), CalculationInput(expression="2+2")))
    assert not requests


def test_app_id_never_logged(chat, monkeypatch, capsys):
    requests = []
    marker = "synthetic-private-app-id"
    mock_service(monkeypatch, requests, "4")
    asyncio.run(
        check_calculation(
            configured(chat, marker), CalculationInput(expression="2+2", craft_result="4")
        )
    )
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    assert marker not in str(requests[0].url)
    assert requests[0].headers["Authorization"] == "Bearer " + marker


@pytest.mark.live
def test_live_wolfram(chat):
    key = read_env().get("CRAFT_WOLFRAM_APP_ID", "")
    if not key:
        pytest.skip("Wolfram App ID is not configured")
    result = asyncio.run(
        check_calculation(configured(chat, key), CalculationInput(expression="2+2"))
    )
    assert "4" in result.wolfram_result


def test_wolfram_timeout_retries_once_with_safe_error(chat, monkeypatch):
    from app.tools.service import Unavailable

    original = httpx.AsyncClient
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("synthetic-private-app-id", request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(timeout), **kwargs),
    )
    with pytest.raises(Unavailable) as stopped:
        asyncio.run(check_calculation(configured(chat), CalculationInput(expression="2+2")))
    assert len(attempts) == 2 and "synthetic-private-app-id" not in str(stopped.value)


def test_numeric_exponents_and_mismatched_units(chat, monkeypatch):
    requests = []
    mock_service(monkeypatch, requests, "1000 N")
    result = asyncio.run(
        check_calculation(
            configured(chat), CalculationInput(expression="1e3 N", craft_result="1000 lbf")
        )
    )
    assert not result.agrees and len(requests) == 1
