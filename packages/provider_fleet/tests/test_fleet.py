import asyncio
import json

import httpx

from craft_fleet.core import JobQueue, LocalResearchFleet, ProviderClient, ProviderError


def spec(provider="deepseek"):
    return {
        "provider": provider,
        "topic": "Summarize the provided fact",
        "source_excerpts": [{"source_id": "s1", "text": "The synthetic value is 12 V."}],
        "max_output_tokens": 512,
        "deadline": 20,
    }


def registry():
    return {
        "version": 1,
        "max_concurrency": 2,
        "key_ref": "keys.env:KEY",
        "providers": {
            "luna": {
                "deployment": "gpt-6-luna",
                "endpoint": "https://synthetic.invalid/responses",
                "protocol": "responses",
            },
            "deepseek": {
                "deployment": "DeepSeek-V4-Flash",
                "endpoint": "https://synthetic.invalid/chat",
                "protocol": "chat",
            },
        },
    }


def test_queue_claim_pause_cancel_and_global_cap(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite")
    first = queue.add(spec())
    second = queue.add(spec())
    third = queue.add(spec("luna"))
    queue.pause()
    assert queue.claim("worker") is None
    queue.resume()
    first_claim = queue.claim("worker")
    third_claim = queue.claim("worker2")
    assert first_claim is not None and first_claim["id"] == first
    assert third_claim is not None and third_claim["id"] == third
    assert queue.claim("worker3") is None
    queue.cancel(second)
    queue.finish(first, "completed", {}, {})
    assert queue.claim("worker") is None
    cancelled = queue.show(second)
    assert cancelled is not None and cancelled["status"] == "cancelled"


def test_rotating_key_and_protocol_normalization(tmp_path):
    async def run():
        keyfile = tmp_path / "keys.env"
        keyfile.write_text("KEY=first-synthetic-key\n")
        requests = []

        def answer(request):
            requests.append(request)
            if request.url.path == "/responses":
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "model": "gpt-6-luna",
                        "usage": {"input_tokens": 1},
                        "output": [
                            {
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": '{"summary":"fact","findings":[]}',
                                    }
                                ]
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "model": "DeepSeek-V4-Flash",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"summary":"fact","findings":[]}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 1},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            client = ProviderClient(registry(), tmp_path, http_client=http)
            assert (await client.infer("luna", spec("luna")))["successful"]
            keyfile.write_text("KEY=second-synthetic-key\n")
            assert (await client.infer("deepseek", spec()))["successful"]
        assert requests[0].headers["api-key"] == "first-synthetic-key"
        assert requests[1].headers["api-key"] == "second-synthetic-key"
        assert all("synthetic-key" not in str(request.url) for request in requests)
        assert all("synthetic-key" not in request.content.decode() for request in requests)

    asyncio.run(run())


def test_fake_research_batch_and_evidence_guard(tmp_path):
    class Fake:
        async def infer(self, provider, task):
            result = {"summary": "fact", "findings": [{"source_id": "s1", "quote": "12 V."}]}
            return {
                "successful": True,
                "content": json.dumps(result),
                "usage": {"total_tokens": 1},
                "model": provider,
            }

    async def run():
        fleet = LocalResearchFleet(
            tmp_path / "jobs.sqlite", registry(), tmp_path, provider_client=Fake()
        )
        ids = [fleet.queue.add(spec()), fleet.queue.add(spec("luna"))]
        await fleet.run_queue()
        results = [fleet.queue.show(identifier) for identifier in ids]
        assert all(result is not None and result["status"] == "completed" for result in results)
        _, status = fleet._validate_evidence(
            spec()["source_excerpts"],
            {"findings": [{"source_id": "unknown", "quote": "not found"}]},
        )
        assert status == "needs_review"

    asyncio.run(run())


def test_timeout_and_auth_failure_are_not_replayed(tmp_path):
    class Fake:
        def __init__(self, kind):
            self.calls = 0
            self.kind = kind

        async def infer(self, *args):
            self.calls += 1
            raise ProviderError(self.kind, status=401 if self.kind == "http_status" else None)

    async def run():
        for kind in ["timeout", "http_status"]:
            fake = Fake(kind)
            fleet = LocalResearchFleet(
                tmp_path / (kind + ".sqlite"), registry(), tmp_path, provider_client=fake
            )
            identifier = fleet.queue.add(spec())
            await fleet.run_queue()
            assert fake.calls == 1
            result = fleet.queue.show(identifier)
            assert result is not None and result["status"] in {"uncertain", "failed"}

    asyncio.run(run())


def test_running_cancel_interrupts_request_and_preserves_uncertain_record(tmp_path):
    class Waiting:
        def __init__(self):
            self.entered = asyncio.Event()
            self.cancelled = False

        async def infer(self, *args):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def run():
        client = Waiting()
        fleet = LocalResearchFleet(
            tmp_path / "jobs.sqlite", registry(), tmp_path, provider_client=client
        )
        identifier = fleet.queue.add(spec())
        task = asyncio.create_task(fleet.run_queue())
        await client.entered.wait()
        fleet.queue.cancel(identifier)
        await task
        result = fleet.queue.show(identifier)
        assert result is not None and result["status"] == "cancelled" and client.cancelled
        assert result["artifacts"] == []

    asyncio.run(run())


def test_terminal_job_cannot_be_overwritten(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite")
    identifier = queue.add(spec())
    queue.claim("worker")
    queue.finish(identifier, "completed", {"summary": "first"}, {})
    import pytest

    with pytest.raises(ValueError, match="not_running"):
        queue.finish(identifier, "failed", {"summary": "changed"}, {})
    result = queue.show(identifier)
    assert result is not None and result["status"] == "completed"


def test_malformed_and_oversized_provider_output_have_safe_errors(tmp_path):
    import pytest

    async def run():
        (tmp_path / "keys.env").write_text("KEY=synthetic-secret\n")
        for response in [
            httpx.Response(200, text="invalid-json"),
            httpx.Response(200, content=b"x" * 100),
        ]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request, response=response: response)
            ) as http:
                client = ProviderClient(registry(), tmp_path, http_client=http, max_body_bytes=50)
                with pytest.raises(ProviderError):
                    await client.infer("luna", spec("luna"))

    asyncio.run(run())


def test_scheduler_cancel_closes_inflight_request(tmp_path):
    class Waiting:
        def __init__(self):
            self.entered = asyncio.Event()
            self.closed = asyncio.Event()

        async def infer(self, *args):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.closed.set()

    async def run():
        client = Waiting()
        fleet = LocalResearchFleet(
            tmp_path / "cancel.sqlite", registry(), tmp_path, provider_client=client
        )
        identifier = fleet.queue.add(spec())
        task = asyncio.create_task(fleet.run_queue())
        await client.entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(client.closed.wait(), 1)
        result = fleet.queue.show(identifier)
        assert result is not None and result["status"] == "cancelled"

    asyncio.run(run())
