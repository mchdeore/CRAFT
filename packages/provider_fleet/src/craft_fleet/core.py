from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from craft_fleet.deliverables import build_job_request, validate_deliverable
from craft_fleet.pricing import Price, estimate_cost, safe_id
from craft_fleet.queue import JobQueue as JobQueue


@dataclass
class ProviderError(Exception):
    kind: str
    status: int | None = None
    retry_after: float | None = None
    request_id: str | None = None


class ProviderClient:
    def __init__(
        self,
        registry: dict[str, Any],
        trusted_config_dir: Path,
        *,
        http_client: httpx.AsyncClient | None = None,
        max_body_bytes: int = 1_000_000,
    ):
        self.registry = registry
        self.trusted_config_dir = Path(trusted_config_dir)
        self.max_body_bytes = max_body_bytes
        self.http_client = http_client

    def _provider_cfg(self, name: str) -> dict[str, Any]:
        p = self.registry.get("providers", {}).get(name)
        if not p:
            raise ProviderError("bad_provider")
        endpoint = str(p.get("endpoint", ""))
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderError("bad_endpoint")
        return p

    def _load_key(self) -> str:
        ref = self.registry.get("key_ref", "")
        f, key_name = ref.split(":", 1)
        if Path(f).name != f:
            raise ProviderError("bad_key_reference")
        data = dict(
            line.split("=", 1)
            for line in (self.trusted_config_dir / f).read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        key = data.get(key_name)
        if not isinstance(key, str) or not key:
            raise ProviderError("missing_key")
        return key

    async def infer(self, provider_name: str, spec: dict[str, Any]) -> dict[str, Any]:
        cfg = self._provider_cfg(provider_name)
        key = self._load_key()
        deployment = cfg.get("deployment")
        if not isinstance(deployment, str) or not deployment:
            raise ProviderError("bad_deployment")
        protocol = str(cfg["protocol"])
        if protocol not in {"responses", "chat"}:
            raise ProviderError("bad_protocol")
        body = build_job_request(protocol, deployment, spec)
        headers = {"api-key": key, "content-type": "application/json"}
        client = self.http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(spec["deadline"])), follow_redirects=False, trust_env=False
        )
        own = self.http_client is None
        try:
            data, request_id = await self._post_json(client, cfg["endpoint"], headers, body)
            result = self._normalize(protocol, data)
            result.update(request_id=request_id, response_id=safe_id(data.get("id")))
            return result
        finally:
            if own:
                await client.aclose()

    async def _post_json(
        self, client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        request_id = None
        try:
            async with client.stream(
                "POST", url, headers=headers, content=json.dumps(body), follow_redirects=False
            ) as r:
                request_id = safe_id(
                    r.headers.get("apim-request-id")
                    or r.headers.get("x-request-id")
                    or r.headers.get("x-ms-request-id")
                )
                if r.status_code in (401, 403, 429):
                    ra = r.headers.get("retry-after")
                    raise ProviderError(
                        "http_status",
                        status=r.status_code,
                        retry_after=float(ra) if ra and ra.isdigit() else None,
                        request_id=request_id,
                    )
                if r.status_code != 200:
                    raise ProviderError("http_status", status=r.status_code, request_id=request_id)
                b = bytearray()
                async for chunk in r.aiter_bytes():
                    b.extend(chunk)
                    if len(b) > self.max_body_bytes:
                        raise ProviderError("body_too_large")
            data = json.loads(bytes(b).decode("utf-8"))
            if not isinstance(data, dict):
                raise ProviderError("bad_response_shape")
            return data, request_id
        except TimeoutError:
            raise ProviderError("timeout", request_id=request_id) from None
        except httpx.TimeoutException:
            raise ProviderError("timeout", request_id=request_id) from None
        except httpx.HTTPError:
            raise ProviderError("transport", request_id=request_id) from None
        except json.JSONDecodeError:
            raise ProviderError("bad_json") from None
        except UnicodeError:
            raise ProviderError("bad_encoding") from None

    def _normalize(self, protocol: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._normalize_shape(protocol, data)
        except (AttributeError, TypeError, IndexError):
            raise ProviderError("bad_response_shape") from None

    def _normalize_shape(self, protocol: str, data: dict[str, Any]) -> dict[str, Any]:
        if protocol == "responses":
            content = "".join(
                part.get("text", "")
                for item in data.get("output", [])
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            finish = str(data.get("status") or "")
            model = data.get("model")
            usage = data.get("usage") or {}
        else:
            ch = (data.get("choices") or [{}])[0] or {}
            content = (ch.get("message") or {}).get("content") or ""
            finish = str(ch.get("finish_reason") or "")
            model = data.get("model")
            usage = data.get("usage") or {}
        if not isinstance(content, str) or not isinstance(usage, dict):
            raise ProviderError("bad_response_shape")
        successful = finish in {"completed", "stop"} and bool(content)
        return {
            "content": content,
            "finish_status": finish,
            "model": model,
            "usage": usage,
            "successful": successful,
        }


class InferenceProvider(Protocol):
    async def infer(self, provider_name: str, spec: dict[str, Any], /) -> dict[str, Any] | None: ...


class LocalResearchFleet:
    def __init__(
        self,
        db_path: Path,
        registry: dict[str, Any],
        trusted_config_dir: Path,
        *,
        provider_client: InferenceProvider | None = None,
    ):
        from uuid import uuid4

        limits = registry.get("limits", {})
        self.queue = JobQueue(
            db_path,
            provider_deployments={
                name: cfg["deployment"] for name, cfg in registry.get("providers", {}).items()
            },
            max_global=registry.get("max_concurrency", 2),
            identity="run-" + uuid4().hex,
            run_token_cap=limits.get("run_tokens", 60000),
            day_token_cap=limits.get("day_tokens", 200000),
            per_minute_request_limit=limits.get("requests_per_minute"),
            per_minute_token_limit=limits.get("tokens_per_minute"),
        )
        self.registry = registry
        self.prices = {
            name: Price.from_config(cfg["price"]) if cfg.get("price") else None
            for name, cfg in registry.get("providers", {}).items()
        }
        self.provider = provider_client or ProviderClient(registry, trusted_config_dir)
        self._running: dict[int, asyncio.Task[Any]] = {}

    def _validate_evidence(
        self, source_excerpts: list[dict[str, str]], result: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        validated, status, _ = validate_deliverable({"source_excerpts": source_excerpts}, result)
        return validated or result, status

    async def _execute_job(self, job: dict[str, Any]) -> None:
        jid, spec = job["id"], job["spec"]
        response_meta: dict[str, Any] = {}

        def finish(job_id, status, artifact, meta):
            self.queue.finish(
                job_id, status, artifact, response_meta | meta | {"claim_token": job["claim_token"]}
            )

        if self.queue.is_cancel_requested(jid):
            finish(jid, "cancelled", None, {"reason": "cancel_requested"})
            return

        async def bounded_request():
            try:
                return await asyncio.wait_for(
                    self.provider.infer(spec["provider"], spec), timeout=float(spec["deadline"])
                )
            except TimeoutError:
                raise ProviderError("timeout") from None

        req = asyncio.create_task(bounded_request())
        try:
            while not req.done():
                await asyncio.sleep(0.2)
                if self.queue.is_cancel_requested(jid):
                    req.cancel()
                    try:
                        await req
                    except asyncio.CancelledError:
                        pass
                    finish(
                        jid,
                        "cancelled",
                        None,
                        {"reason": "cancel_requested_running", "uncertain": True},
                    )
                    return
            resp = await req
            if not isinstance(resp, dict):
                finish(jid, "failed", None, {"reason": "invalid_provider_result"})
                return
            response_meta.update(
                {
                    "usage": resp.get("usage"),
                    "model": resp.get("model"),
                    "finish": resp.get("finish_status"),
                    "request_id": resp.get("request_id"),
                    "response_id": resp.get("response_id"),
                    "estimated_cost": estimate_cost(
                        resp.get("usage"), self.prices.get(spec["provider"])
                    ),
                }
            )
            if not resp.get("successful"):
                finish(
                    jid,
                    "failed",
                    None,
                    {"reason": "incomplete", "finish": resp.get("finish_status")},
                )
                return
            raw = resp.get("content", "")
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                finish(
                    jid,
                    "needs_review",
                    None,
                    {"reason": "invalid_research_json", "usage": resp.get("usage", {})},
                )
                return
            if not isinstance(parsed, dict):
                finish(jid, "needs_review", None, {"reason": "invalid_research_shape"})
                return
            parsed, status, reason = validate_deliverable(spec, parsed)
            finish(
                jid,
                status,
                parsed,
                {"usage": resp.get("usage", {}), "model": resp.get("model"), "reason": reason},
            )
        except ProviderError as e:
            if (
                e.kind == "http_status"
                and e.status == 429
                and job["attempts"]
                < min(self.queue.max_retry_revisions, spec.get("retry_limit", 2) + 1)
            ):
                after = min(30.0, max(0.1, e.retry_after or 1.0))
                self.queue.requeue_with_retry(
                    jid,
                    after,
                    "rate_limited",
                    claim_token=job["claim_token"],
                    request_id=e.request_id,
                )
                return
            finish(
                jid,
                "uncertain" if e.kind in {"timeout", "transport"} else "failed",
                None,
                {
                    "provider_error": e.kind,
                    "request_id": e.request_id,
                    "status": e.status,
                    "retry_after": e.retry_after,
                    "accepted": False
                    if e.kind == "http_status" and e.status in {401, 403, 429}
                    else None,
                },
            )
        except asyncio.CancelledError:
            finish(jid, "cancelled", None, {"reason": "scheduler_cancelled", "uncertain": True})
            raise
        except Exception:
            finish(jid, "failed", None, {"reason": "execution_error"})
        finally:
            if not req.done():
                req.cancel()
                try:
                    await req
                except (asyncio.CancelledError, Exception):
                    pass

    async def run_queue(
        self, stop_event: asyncio.Event | None = None, poll_interval: float = 0.5
    ) -> None:
        max_global = min(2, int(self.registry.get("max_concurrency", 2)))

        async def launch_one() -> bool:
            job = self.queue.claim("runner")
            if not job:
                return False

            async def wrapped() -> None:
                try:
                    await self._execute_job(job)
                finally:
                    self._running.pop(job["id"], None)

            self._running[job["id"]] = asyncio.create_task(wrapped())
            return True

        try:
            while True:
                if stop_event and stop_event.is_set() and not self._running:
                    return
                while len(self._running) < max_global and not (stop_event and stop_event.is_set()):
                    if not await launch_one():
                        break
                if self._running:
                    done, _ = await asyncio.wait(
                        self._running.values(),
                        timeout=poll_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in done:
                        await t
                else:
                    delay = self.queue.next_ready_delay()
                    if delay is None or (stop_event and stop_event.is_set()):
                        return
                    await asyncio.sleep(min(1.0, delay))
        finally:
            tasks = list(self._running.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
