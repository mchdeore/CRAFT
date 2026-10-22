"""Queued OCR enrichment for indexed document versions."""

from __future__ import annotations

import base64
import hashlib
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
from redis import Redis
from redis.exceptions import RedisError, WatchError
from rq import Queue, get_current_job
from rq.job import Job
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError, InvalidInput, NotFound, TooLarge
from app.infrastructure.provider import get_provider
from app.library import queue, service

TERMINAL_TTL = 7 * 24 * 60 * 60


class OcrPermanentError(InvalidInput):
    """A rejected source requires an explicit operator retry, not another scan."""


def supports_ocr(media_type: str) -> bool:
    return media_type == "application/pdf" or media_type.startswith("image/")


def retry_delay(settings: Settings, attempts: int) -> int:
    # Explicit operator retries may exceed the automatic attempt limit.
    return min(86400, settings.ocr_retry_seconds * 2 ** min(max(attempts - 1, 0), 17))


@dataclass(frozen=True)
class OcrExtraction:
    model: str
    text: str
    page_count: int


class OcrClient(Protocol):
    def extract(self, *, content: bytes, media_type: str) -> OcrExtraction: ...


def _extract_text(body: Any) -> tuple[str, int]:
    if not isinstance(body, dict):
        raise ApiError("OCR response was not an object.")
    pages = body.get("pages")
    chunks: list[str] = []
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            for key in ("markdown", "text", "content"):
                value = page.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
                    break
    if not chunks:
        for key in ("text", "markdown", "content"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
                break
    text = "\n\n".join(chunks).strip()
    if not text:
        raise ApiError("OCR response did not include extractable text.")
    page_count = max(1, len(pages)) if isinstance(pages, list) else 1
    return text, page_count


class FoundryOcrClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, *, content: bytes, media_type: str) -> OcrExtraction:
        if not supports_ocr(media_type):
            raise OcrPermanentError("OCR source format is not supported.")
        if len(content) > self.settings.ocr_max_bytes:
            raise OcrPermanentError("OCR source exceeds configured byte limit.")
        document_key = "image_url" if media_type.startswith("image/") else "document_url"
        payload = {
            "model": self.settings.model_ocr,
            "document": {
                "type": document_key,
                document_key: "data:"
                + media_type
                + ";base64,"
                + base64.b64encode(content).decode("ascii"),
            },
            "include_image_base64": False,
        }
        endpoint = self.settings.model_endpoint.rstrip("/") + "/providers/mistral/azure/ocr"
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": "Bearer " + self.settings.model_key},
                json=payload,
            )
        if response.status_code >= 400:
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                raise OcrPermanentError("OCR provider rejected the request.")
            raise ApiError("OCR request failed.")
        try:
            body = response.json()
        except ValueError:
            raise ApiError("OCR response was not valid JSON.") from None
        text, page_count = _extract_text(body)
        return OcrExtraction(model=self.settings.model_ocr, text=text, page_count=page_count)


def _find_media_type(record: dict[str, Any], version: int) -> str:
    selected = next((value for value in record["versions"] if value["version"] == version), None)
    if selected is None:
        raise NotFound()
    media_type = selected.get("media_type")
    return media_type if isinstance(media_type, str) and media_type else "application/octet-stream"


def _read_ocr_text(settings: Settings, item: str, version: int) -> str | None:
    sessions, engine = make_owner_sessions(settings)
    try:
        with unit_of_work(sessions, read_only=True) as session:
            return session.execute(
                select(m.doc_versions.c.ocr_text).where(
                    m.doc_versions.c.item == item, m.doc_versions.c.version == version
                )
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def ocr_version(
    settings: Settings, item: str, version: int, *, client: OcrClient | None = None
) -> dict[str, Any]:
    provider = get_provider(settings.provider_root)
    try:
        record = provider.get_item(item)
    except (KeyError, FileNotFoundError):
        raise NotFound() from None
    if record["deleted_at"] is not None:
        raise NotFound()
    media_type = _find_media_type(record, version)
    if not supports_ocr(media_type):
        raise OcrPermanentError("OCR source format is not supported.")
    try:
        content = provider.read_content(item, version, max_bytes=settings.ocr_max_bytes)
    except (KeyError, FileNotFoundError):
        raise NotFound() from None
    except ValueError:
        raise OcrPermanentError("OCR source exceeds configured byte limit.") from None
    sessions, engine = make_owner_sessions(settings)
    try:
        with unit_of_work(sessions, read_only=True) as session:
            indexed = session.execute(
                select(m.doc_versions.c.sha256)
                .join(m.provider_items, m.provider_items.c.item == m.doc_versions.c.item)
                .where(
                    m.doc_versions.c.item == item,
                    m.doc_versions.c.version == version,
                    m.provider_items.c.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
        original = next(row for row in record["versions"] if row["version"] == version)
        digest = hashlib.sha256(content).hexdigest()
        if indexed is None or digest != bytes(indexed).hex() or digest != original["sha256"]:
            raise NotFound()
        extraction = (client or FoundryOcrClient(settings)).extract(
            content=content, media_type=media_type
        )
        with unit_of_work(sessions) as session:
            # Serialize final persistence with provider deletion, not with inference.
            active = session.execute(
                select(m.provider_items.c.item)
                .where(m.provider_items.c.item == item, m.provider_items.c.deleted_at.is_(None))
                .with_for_update()
            ).scalar_one_or_none()
            if active is None:
                raise NotFound()
            updated = session.execute(
                m.doc_versions.update()
                .where(
                    m.doc_versions.c.item == item,
                    m.doc_versions.c.version == version,
                    m.doc_versions.c.sha256 == bytes.fromhex(digest),
                )
                .values(
                    ocr_text=extraction.text,
                    ocr_model=extraction.model,
                    ocr_updated_at=datetime.now(UTC),
                )
                .returning(m.doc_versions.c.id)
            ).scalar_one_or_none()
            if updated is None:
                raise NotFound()
    finally:
        engine.dispose()
    return {
        "item": item,
        "version": version,
        "model": extraction.model,
        "page_count": extraction.page_count,
        "characters": len(extraction.text),
    }


def run_ocr_job(
    *,
    item: str,
    version: int,
    force: bool = False,
    requested_by: str | None = None,
    retry_of: str | None = None,
) -> dict[str, Any]:
    _ = (requested_by, retry_of)
    settings = Settings.from_env()
    if not force and _read_ocr_text(settings, item, version) is not None:
        return {"item": item, "version": version, "status": "skipped"}
    try:
        result = ocr_version(settings, item, version)
    except Exception as error:
        job = get_current_job()
        if job is not None:
            policy = f"craft:ocr:{job.origin}:{item}:{version}:policy"
            attempts = int(job.meta.get("ocr_attempt", 1))
            delay = retry_delay(settings, attempts)
            try:
                job.connection.hset(
                    policy,
                    mapping={
                        "permanent": int(isinstance(error, (OcrPermanentError, NotFound))),
                        "retry_after": time.time() + delay,
                    },
                )
            except RedisError:
                # Keep the OCR error as the job failure; the policy is written on the next failure.
                pass
        raise
    result["status"] = "updated"
    return result


def expire_terminal_claim(job: Job, connection: Redis, *args: Any, **kwargs: Any) -> None:
    """RQ completion callbacks retain diagnostics without retaining claims forever."""
    identity = f"craft:ocr:{job.origin}:{job.kwargs['item']}:{job.kwargs['version']}"
    try:
        with connection.pipeline() as pipe:
            for _ in range(8):
                try:
                    pipe.watch(identity)
                    if pipe.get(identity) != job.id.encode():
                        return
                    pipe.multi()
                    pipe.expire(identity, TERMINAL_TTL)
                    pipe.expire(identity + ":policy", TERMINAL_TTL)
                    pipe.execute()
                    return
                except WatchError:
                    pipe.reset()
    except RedisError:
        # A later enqueue also expires stale terminal claims; do not mask OCR results.
        return


def policy_values(state: dict[bytes, bytes], *, existing: bool) -> tuple[int, float]:
    try:
        attempts = int(state.get(b"attempts", 1 if existing else 0))
        retry_after = float(state.get(b"retry_after", 0))
        if (
            not 0 <= attempts <= 1_000_000
            or not math.isfinite(retry_after)
            or retry_after < 0
            or state.get(b"permanent", b"0") not in {b"0", b"1"}
        ):
            raise ValueError
        return attempts, retry_after
    except (ValueError, TypeError, OverflowError):
        raise queue.QueueUnavailable() from None


def recover_version(caller: Caller, item: str, version: int) -> dict[str, Any]:
    settings = queue.queue_settings(caller)
    sessions, _ = service.scope(caller)
    provider = service.source_provider(settings)
    source = service.source_item(provider, item)
    with unit_of_work(sessions, read_only=True) as session:
        _, selected, _ = service.readable_source_version(session, caller, item, version, source)
        expected = bytes(selected["sha256"]).hex()
    if not supports_ocr(_find_media_type(source, version)):
        raise OcrPermanentError("OCR source format is not supported.")
    try:
        content = provider.read_content(item, version, max_bytes=settings.ocr_max_bytes)
    except (KeyError, FileNotFoundError):
        raise NotFound() from None
    except ValueError:
        raise TooLarge() from None
    if hashlib.sha256(content).hexdigest() != expected:
        raise NotFound()
    connection = queue.open_connection(settings)
    try:
        job, _ = enqueue_version_job(
            queue.open_queue(settings, connection),
            settings,
            item,
            version,
            requested_by=str(caller.user_id),
            force=True,
            recovery=True,
        )
        assert job is not None
        return queue.index_job_payload(job)
    except RedisError:
        raise queue.QueueUnavailable() from None
    finally:
        connection.close()


def enqueue_version_job(
    managed: Queue,
    settings: Settings,
    item: str,
    version: int,
    *,
    requested_by: str | None = None,
    force: bool = False,
    retry_of: str | None = None,
    recovery: bool = False,
) -> tuple[Job | None, bool]:
    identity = f"craft:ocr:{managed.name}:{item}:{version}"
    policy = identity + ":policy"
    scope = hashlib.sha256(managed.name.encode()).hexdigest()
    canonical = f"library_ocr_{scope}_{item}_{version}"
    # The claim and RQ enqueue share a transaction, including retry attempts.
    for _ in range(8):
        try:
            with managed.connection.pipeline() as pipe:
                pipe.watch(identity, policy)
                mapped = cast(bytes | None, pipe.get(identity))
                previous_id = mapped.decode() if mapped is not None else canonical
                existing = managed.fetch_job(previous_id)
                if existing is None and mapped is None:
                    legacy = managed.fetch_job(f"library_ocr_{item}_{version}")
                    if legacy is not None and (
                        legacy.origin == managed.name
                        and legacy.func_name == "app.library.enrichment.run_ocr_job"
                        and legacy.kwargs.get("item") == item
                        and legacy.kwargs.get("version") == version
                    ):
                        existing = legacy
                if existing is not None:
                    pipe.watch(existing.key)
                    if (
                        existing.origin != managed.name
                        or existing.func_name != "app.library.enrichment.run_ocr_job"
                        or existing.kwargs.get("item") != item
                        or existing.kwargs.get("version") != version
                    ):
                        raise queue.QueueUnavailable()
                    if queue.status_of(existing) in {"queued", "started", "deferred", "scheduled"}:
                        pipe.multi()
                        pipe.set(identity, existing.id)
                        pipe.persist(policy)
                        pipe.execute()
                        return existing, False
                if (
                    not force
                    and retry_of is None
                    and _read_ocr_text(settings, item, version) is not None
                ):
                    pipe.multi()
                    pipe.expire(identity, TERMINAL_TTL, nx=True)
                    pipe.expire(policy, TERMINAL_TTL, nx=True)
                    pipe.execute()
                    return None, False
                state = cast(dict[bytes, bytes], pipe.hgetall(policy))
                attempts, retry_after = (
                    (0, 0.0) if recovery else policy_values(state, existing=existing is not None)
                )
                now = time.time()
                if (
                    not force
                    and retry_of is None
                    and (
                        state.get(b"permanent") == b"1"
                        or attempts >= settings.ocr_attempt_limit
                        or retry_after > now
                    )
                ):
                    pipe.multi()
                    pipe.expire(identity, TERMINAL_TTL, nx=True)
                    pipe.expire(policy, TERMINAL_TTL, nx=True)
                    pipe.execute()
                    return existing, False
                job_id = (
                    canonical
                    if mapped is None and existing is None
                    else f"{canonical}_{uuid4().hex}"
                )
                pipe.multi()
                job = managed.enqueue_call(
                    run_ocr_job,
                    kwargs={
                        "item": item,
                        "version": version,
                        "force": force,
                        "requested_by": requested_by,
                        "retry_of": retry_of,
                    },
                    job_id=job_id,
                    timeout=30 * 60,
                    result_ttl=7 * 24 * 60 * 60,
                    failure_ttl=7 * 24 * 60 * 60,
                    meta={"ocr_attempt": attempts + 1},
                    on_success=expire_terminal_claim,
                    on_failure=expire_terminal_claim,
                    on_stopped=expire_terminal_claim,
                    pipeline=pipe,
                )
                # Do not expire a claim while a queued job may still be active.
                pipe.set(identity, job.id)
                pipe.hset(
                    policy,
                    mapping={
                        "attempts": attempts + 1,
                        "permanent": 0,
                        # Retain a cooldown even if the worker dies before recording failure.
                        "retry_after": now + 30 * 60 + retry_delay(settings, attempts + 1),
                    },
                )
                pipe.persist(policy)
                pipe.execute()
                return job, True
        except WatchError:
            continue
    raise queue.QueueUnavailable()


def enqueue_version_jobs(
    settings: Settings,
    versions: Iterable[tuple[str, int]],
    *,
    requested_by: str | None = None,
    force: bool = False,
) -> int:
    connection = queue.open_connection(settings)
    try:
        managed = queue.open_queue(settings, connection)
        scheduled = 0
        unique = sorted({(item, int(version)) for item, version in versions})
        for item, version in unique:
            _, added = enqueue_version_job(
                managed, settings, item, version, requested_by=requested_by, force=force
            )
            scheduled += int(added)
        return scheduled
    except RedisError:
        raise queue.QueueUnavailable() from None
    finally:
        connection.close()
