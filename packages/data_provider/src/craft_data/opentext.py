from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse

import httpx

from .models import BlockInput
from .simulator import RemoteListing, TenantPage, TenantSource


class OpenTextError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class MissingExtractionCapability(OpenTextError):
    def __init__(self) -> None:
        super().__init__("missing_extraction_capability")


def _safe_error(status_code: int) -> OpenTextError:
    if status_code == 401:
        return OpenTextError("credential_expired", status_code)
    if status_code == 403:
        return OpenTextError("access_denied", status_code)
    return OpenTextError("upstream_error", status_code)


def _validate_https_base_url(value: str, *, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{name} must be https")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not include userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not include query or fragment")
    if not parsed.netloc:
        raise ValueError(f"{name} must include host")
    path = parsed.path.rstrip("/")
    return f"https://{parsed.netloc}{path}"


def _collection(data):
    embedded = data.get("_embedded")
    if not isinstance(embedded, dict) or not isinstance(embedded.get("collection"), list):
        raise OpenTextError("malformed_collection")
    return embedded["collection"]


class OpenTextTenant(TenantSource):
    def __init__(
        self,
        cms_url: str,
        css_url: str,
        root_type: str,
        root_id: str,
        mission: str,
        classification: str,
        token_provider: Callable[[], str],
        http_client: httpx.Client | None = None,
        *,
        silo: str | None = None,
        stage: str | None = None,
        synthetic: bool = False,
        provider: str = "opentext",
        max_item_bytes: int = 8 * 1024 * 1024,
        max_json_bytes: int = 1024 * 1024,
    ) -> None:
        self.cms_url = _validate_https_base_url(cms_url, name="cms_url")
        self.css_url = _validate_https_base_url(css_url, name="css_url")
        self.root_type = root_type
        self.root_id = root_id
        self.scope_id = root_id
        self.mission = mission
        self.classification = classification
        self.silo = silo
        self.stage = stage
        self.synthetic = synthetic
        self.provider = provider
        self._token_provider = token_provider
        self._max_item_bytes = max_item_bytes
        self._max_json_bytes = max_json_bytes
        self._metadata_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._type_cache: dict[tuple[str, str], str] = {}
        self._extract_method: dict[tuple[str, str], str] = {}
        if any(
            type(value) is not int or not 1 <= value <= 32 * 1024 * 1024
            for value in (max_item_bytes, max_json_bytes)
        ):
            raise ValueError("invalid_byte_limit")
        if type(synthetic) is not bool or not callable(token_provider):
            raise ValueError("invalid_configuration")
        if any(
            not isinstance(value, str) or not value
            for value in (root_type, root_id, mission, classification, provider)
        ):
            raise ValueError("invalid_configuration")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(trust_env=False, follow_redirects=False)

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token:
                raise ValueError()
            return {"Authorization": "Bearer " + token}
        except Exception:
            raise OpenTextError("credential_unavailable") from None

    def _bounded_json_get(self, url: str) -> dict[str, Any]:
        try:
            with self._client.stream(
                "GET", url, headers=self._headers(), follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise _safe_error(response.status_code)
                data = bytearray()
                for piece in response.iter_bytes():
                    if len(data) + len(piece) > self._max_json_bytes:
                        raise OpenTextError("json_too_large")
                    data.extend(piece)
            value = httpx.Response(200, content=bytes(data)).json()
            if not isinstance(value, dict):
                raise OpenTextError("malformed_json")
            return value
        except OpenTextError:
            raise
        except Exception:
            raise OpenTextError("metadata_unavailable") from None

    def _cursor_allowed(self, cursor: str) -> None:
        cur = urlparse(cursor)
        base = urlparse(self.cms_url)
        if any(part in {".", ".."} for part in unquote(cur.path).split("/")):
            raise ValueError("unsafe_cursor_path")
        if cur.scheme != "https" or cur.netloc != base.netloc:
            raise ValueError("cursor must stay on cms origin")
        if not cur.path.startswith(base.path.rstrip("/") + "/") and cur.path != base.path:
            raise ValueError("cursor must stay on cms path prefix")

    def list_page(self, cursor: str | None) -> TenantPage:
        if cursor is None:
            path = (
                f"/instances/folder/{quote(self.root_type, safe='')}/"
                f"{quote(self.root_id, safe='')}/items"
            )
            query = urlencode({"recursive": "true"})
            url = f"{self.cms_url}{path}?{query}"
        else:
            self._cursor_allowed(cursor)
            url = cursor

        data = self._bounded_json_get(url)
        coll = _collection(data)
        if not isinstance(coll, list):
            raise OpenTextError("malformed_json")

        items: list[RemoteListing] = []
        for raw in coll:
            if not isinstance(raw, dict):
                raise OpenTextError("malformed_listing")
            category = raw.get("category")
            if category != "file":
                continue
            if (
                raw.get("deleted") is True
                or raw.get("hidden") is True
                or raw.get("latest") is False
            ):
                continue
            chronicle = raw.get("chronicle_id")
            version_id = raw.get("id")
            item_type = raw.get("type")
            if any(
                not isinstance(value, str) or not value
                for value in (chronicle, version_id, item_type)
            ):
                raise OpenTextError("malformed_listing")
            assert (
                isinstance(chronicle, str)
                and isinstance(version_id, str)
                and isinstance(item_type, str)
            )
            self._type_cache[(chronicle, version_id)] = item_type
            source_location = (
                f"{self.cms_url}/instances/file/{quote(item_type, safe='')}/"
                f"{quote(version_id, safe='')}"
            )
            items.append(
                RemoteListing(
                    provider=self.provider,
                    remote_id=str(chronicle),
                    scope_id=self.root_id,
                    name=str(raw.get("display_name") or raw.get("name") or chronicle),
                    source_location=source_location,
                    mission=self.mission,
                    silo=self.silo,
                    stage=self.stage,
                    classification=self.classification,
                    synthetic=self.synthetic,
                    current_version=str(version_id),
                )
            )

        next_link = ((data.get("_links") or {}).get("next") or {}).get("href")
        next_cursor = str(next_link) if isinstance(next_link, str) else None
        return TenantPage(items=tuple(items), next_cursor=next_cursor)

    def _version_metadata(self, remote_id: str, provider_version: str) -> dict[str, Any]:
        cached = self._metadata_cache.get((remote_id, provider_version))
        if cached is not None:
            return cached
        item_type = self._type_cache.get((remote_id, provider_version))
        if item_type is None:
            raise OpenTextError("unknown_item_type")
        path = f"/instances/file/{quote(item_type, safe='')}/{quote(provider_version, safe='')}"
        data = self._bounded_json_get(f"{self.cms_url}{path}")
        chronicle = data.get("chronicle_id")
        if chronicle != remote_id or data.get("id") != provider_version:
            raise OpenTextError("unstable_snapshot")
        self._metadata_cache.clear()
        self._metadata_cache[(remote_id, provider_version)] = data
        return data

    def list_versions(self, remote_id: str, current_version: str) -> tuple[str, ...]:
        item_type = self._type_cache.get((remote_id, current_version))
        if item_type is None:
            raise OpenTextError("unknown_item_type")
        url = (
            f"{self.cms_url}/instances/file/{quote(item_type, safe='')}/"
            f"{quote(current_version, safe='')}/versions"
        )
        seen = set()
        versions = []
        while url:
            if url in seen or len(seen) >= 100:
                raise OpenTextError("version_page_limit")
            seen.add(url)
            result = self._bounded_json_get(url)
            for item in _collection(result):
                if (
                    not isinstance(item, dict)
                    or item.get("chronicle_id") != remote_id
                    or not isinstance(item.get("id"), str)
                ):
                    raise OpenTextError("unstable_snapshot")
                version = item["id"]
                self._type_cache[(remote_id, version)] = item_type
                if version not in versions:
                    versions.append(version)
                if len(versions) > 10000:
                    raise OpenTextError("version_limit")
            next_link = ((result.get("_links") or {}).get("next") or {}).get("href")
            if next_link is None:
                break
            if not isinstance(next_link, str):
                raise OpenTextError("malformed_cursor")
            self._cursor_allowed(next_link)
            url = next_link
        return tuple(versions)

    def _renditions(self, remote_id: str, provider_version: str) -> list[dict[str, Any]]:
        md = self._version_metadata(remote_id, provider_version)
        renditions = md.get("renditions")
        if isinstance(renditions, list):
            return [r for r in renditions if isinstance(r, dict)]
        item_type = str(md.get("type") or self._type_cache.get((remote_id, provider_version), ""))
        path = (
            f"/instances/file/{quote(item_type, safe='')}/"
            f"{quote(provider_version, safe='')}/contents"
        )
        data = self._bounded_json_get(f"{self.cms_url}{path}")
        coll = _collection(data)
        if not isinstance(coll, list):
            raise OpenTextError("malformed_json")
        return [r for r in coll if isinstance(r, dict)]

    def _pick_primary(self, renditions: list[dict[str, Any]]) -> dict[str, Any]:
        primary = [row for row in renditions if row.get("rendition_type") == "primary"]
        if len(primary) != 1:
            raise OpenTextError("ambiguous_primary_rendition")
        return primary[0]

    def _read_blob(self, rendition: dict[str, Any]) -> tuple[tuple[bytes, ...], str]:
        if rendition.get("source") != "FILE_ID":
            raise OpenTextError("unsupported_rendition_source")
        file_id = rendition.get("blob_id")
        if not isinstance(file_id, str) or not file_id:
            raise OpenTextError("missing_blob_id")
        ref = f"FILE_ID:{file_id}:{rendition.get('mime_type', '')}:{rendition.get('name', '')}"
        url = f"{self.css_url}/v3/files/{quote(file_id, safe='')}/stream"
        chunks = []
        total = 0
        try:
            with self._client.stream(
                "GET", url, headers=self._headers(), follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise _safe_error(response.status_code)
                for piece in response.iter_bytes():
                    total += len(piece)
                    if total > self._max_item_bytes:
                        raise OpenTextError("item_too_large")
                    chunks.append(piece)
            return tuple(chunks), ref
        except OpenTextError:
            raise
        except Exception:
            raise OpenTextError("content_unavailable") from None

    def read_content(self, remote_id: str, provider_version: str) -> tuple[bytes, ...]:
        renditions = self._renditions(remote_id, provider_version)
        primary = self._pick_primary(renditions)
        chunks, ref = self._read_blob(primary)
        self._extract_method[(remote_id, provider_version)] = ref
        return chunks

    def _make_block(self, text: str, line_no: int) -> BlockInput:
        return BlockInput(text=text, location=f"derived-rendition line:{line_no}")

    def get_blocks(self, remote_id: str, provider_version: str) -> tuple[BlockInput, ...]:
        renditions = self._renditions(remote_id, provider_version)
        preferred = [
            r
            for r in renditions
            if r.get("mime_type") == "text/plain" and r.get("rendition_type") == "secondary"
        ]
        if not preferred:
            preferred = [
                r
                for r in renditions
                if r.get("mime_type") == "text/plain" and r.get("rendition_type") == "primary"
            ]
        if not preferred:
            raise MissingExtractionCapability()

        chunks, ref = self._read_blob(preferred[0])
        raw = b"".join(chunks)
        if len(raw) > self._max_item_bytes:
            raise OpenTextError("item_too_large")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError:
            raise OpenTextError("extraction_encoding") from None
        self._extract_method[(remote_id, provider_version)] = ref
        lines = text.splitlines() or [text]
        if len(lines) > 10000:
            raise OpenTextError("block_limit")
        return tuple(self._make_block(line, idx + 1) for idx, line in enumerate(lines))

    def extraction_method_for(self, remote_id: str, provider_version: str) -> str:
        return self._extract_method.get((remote_id, provider_version), "")

    def close(self) -> None:
        self._metadata_cache.clear()
        self._type_cache.clear()
        self._extract_method.clear()
        if self._owns_client:
            self._client.close()
