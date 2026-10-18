import asyncio
import json
from pathlib import Path

import httpx
import pytest

from craft_fleet.core import LocalResearchFleet, ProviderClient, ProviderError
from craft_fleet.pricing import Price, estimate_cost


def task():
    return {
        "provider": "sample",
        "topic": "Read provided fact",
        "source_excerpts": [{"source_id": "one", "text": "A sample is 12 V."}],
        "max_output_tokens": 512,
        "deadline": 10,
    }


def configuration():
    return {
        "key_ref": "key.env:KEY",
        "providers": {
            "sample": {
                "deployment": "sample-model",
                "protocol": "responses",
                "endpoint": "https://example.test/responses",
            }
        },
    }


def test_provider_ids_are_retained_in_response_and_refusal(tmp_path: Path):
    (tmp_path / "key.env").write_text("KEY=synthetic-key\n")

    async def run():
        replies = [
            httpx.Response(
                200,
                headers={"apim-request-id": "request-1"},
                json={
                    "id": "response-1",
                    "status": "completed",
                    "model": "sample-model",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"summary":"sample","findings":[]}',
                                }
                            ]
                        }
                    ],
                },
            ),
            httpx.Response(429, headers={"apim-request-id": "refusal-1", "retry-after": "1"}),
        ]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: replies.pop(0))
        ) as client:
            provider = ProviderClient(configuration(), tmp_path, http_client=client)
            result = await provider.infer("sample", task())
            assert result["request_id"] == "request-1" and result["response_id"] == "response-1"
            with pytest.raises(ProviderError) as error:
                await provider.infer("sample", task())
            assert error.value.request_id == "refusal-1"

    asyncio.run(run())


def test_configured_text_price_is_exact_and_unknown_usage_stays_unknown():
    price = Price.from_config(
        {
            "currency": "USD",
            "input_per_million": "2",
            "cached_input_per_million": "0.2",
            "output_per_million": "10",
            "source": "https://example.test/prices",
            "as_of": "2026-10-17",
        }
    )
    cost = estimate_cost(
        {
            "input_tokens": 1000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 500},
            "output_tokens_details": {"reasoning_tokens": 80},
        },
        price,
    )
    assert cost is not None and cost["amount"] == "0.0021"
    assert estimate_cost({"input_tokens": 1000}, price) is None
    assert estimate_cost({"input_tokens": 1000, "output_tokens": 100}, None) is None
    assert (
        estimate_cost(
            {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2}},
            price,
        )
        is None
    )
    with pytest.raises(ValueError):
        Price.from_config({"currency": "USD", "input_per_million": -1})


def test_job_retry_limit_zero_stops_and_safe_ids_cost_reach_events(tmp_path):
    class Refusal:
        calls = 0

        async def infer(self, *args):
            self.calls += 1
            raise ProviderError("http_status", status=429, request_id="quota-1")

    class Reply:
        async def infer(self, *args):
            return {
                "successful": True,
                "content": json.dumps(
                    {
                        "summary": "Sample is 12 V.",
                        "findings": [{"source_id": "one", "quote": "12 V."}],
                    }
                ),
                "request_id": "http-1",
                "response_id": "reply-1",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 500},
                },
            }

    async def run():
        refused = Refusal()
        fleet = LocalResearchFleet(
            tmp_path / "refusal.sqlite", configuration(), tmp_path, provider_client=refused
        )
        identifier = fleet.queue.add(task() | {"retry_limit": 0})
        await fleet.run_queue()
        result = fleet.queue.show(identifier)
        assert result is not None and result["status"] == "failed" and refused.calls == 1
        assert result["events"][-1]["data"]["request_id"] == "quota-1"
        cfg = configuration()
        cfg["providers"]["sample"]["price"] = {
            "currency": "USD",
            "input_per_million": "2",
            "cached_input_per_million": "0.2",
            "output_per_million": "10",
            "source": "https://example.test/prices",
            "as_of": "2026-10-17",
        }
        priced = LocalResearchFleet(
            tmp_path / "priced.sqlite", cfg, tmp_path, provider_client=Reply()
        )
        identifier = priced.queue.add(task())
        await priced.run_queue()
        result = priced.queue.show(identifier)
        assert result is not None
        event = result["events"][-1]["data"]
        assert (
            event["request_id"] == "http-1"
            and event["response_id"] == "reply-1"
            and event["estimated_cost"]["amount"] == "0.0021"
        )

    asyncio.run(run())


def test_price_limits_and_safe_metadata_reject_unbounded_or_payload_fields():
    from craft_fleet.pricing import safe_estimate, safe_id

    base = {
        "currency": "USD",
        "input_per_million": "2",
        "output_per_million": "10",
        "source": "https://example.test/prices",
        "as_of": "2026-10-17",
    }
    for bad in ("1e-999999", "NaN", "Infinity", True, 0.1):
        with pytest.raises(ValueError):
            Price.from_config(base | {"input_per_million": bad})
    assert safe_id("response-123") == "response-123"
    assert safe_id("private source sentence") is None
    assert (
        safe_estimate(
            {
                "amount": "0.1",
                "currency": "USD",
                "source": "https://example.test/prices",
                "as_of": "2026-10-17",
                "estimated": True,
                "prompt": "private data",
            }
        )
        is None
    )


def test_retry_spec_rejects_invalid_limits_without_changing_submitted_job(tmp_path):
    from craft_fleet.queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.sqlite")
    for limit in (-1, 20, True, 1.5):
        with pytest.raises(ValueError):
            queue.add(task() | {"retry_limit": limit})
    assert queue.list() == []


def test_queued_deployment_is_not_silently_replaced_by_new_registry(tmp_path):
    from craft_fleet.queue import JobQueue

    path = tmp_path / "changed.sqlite"
    original = JobQueue(path, provider_deployments={"sample": "old-model"})
    identifier = original.add(task())
    changed = JobQueue(path, provider_deployments={"sample": "new-model"})
    assert changed.claim("worker") is None
    record = changed.show(identifier)
    assert (
        record is not None and record["status"] == "failed" and record["deployment"] == "old-model"
    )
    assert record["events"][-1]["type"] == "provider_changed"


def test_malformed_protocol_shapes_return_safe_provider_error(tmp_path):
    provider = ProviderClient(configuration(), tmp_path)
    for payload in (
        {"output": [None]},
        {"output": {"content": None}},
        {"output": [{"content": [{"type": "output_text", "text": {"payload": "private"}}]}]},
    ):
        with pytest.raises(ProviderError, match="bad_response_shape"):
            provider._normalize("responses", payload)
