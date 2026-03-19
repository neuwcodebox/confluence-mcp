from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv
from markdownify import markdownify as md


class ConfluenceAuthError(RuntimeError):
    pass


class MCPAuthorizationError(RuntimeError):
    pass


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class TTLRUCache:
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 1800) -> None:
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return entry.value

    def set(self, key: str, value: dict[str, Any]) -> None:
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._data[key] = _CacheEntry(value=value, expires_at=expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)


load_dotenv()


API_CACHE = TTLRUCache(
    max_size=int(os.getenv("IN_MEMORY_CACHE_SIZE", "1000")),
    ttl_seconds=int(os.getenv("IN_MEMORY_CACHE_TTL_SECONDS", "1800")),
)


def _http_verify_option() -> bool | str:
    verify_raw = os.getenv("CONFLUENCE_SSL_VERIFY", "true").strip().lower()
    if verify_raw in {"0", "false", "no", "off"}:
        return False

    ca_bundle = os.getenv("CONFLUENCE_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ca_bundle
    return True


class ConfluenceClient:
    def __init__(self, base_url: str, token: str, api_version: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_version = api_version
        self._token_cache_id = hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def from_token(cls, token: str) -> "ConfluenceClient":
        base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("CONFLUENCE_BASE_URL environment variable is required.")
        api_version = os.getenv("CONFLUENCE_API_VERSION", "v2").strip().lower()
        if api_version not in {"v1", "v2"}:
            raise ValueError("CONFLUENCE_API_VERSION must be 'v1' or 'v2'.")
        return cls(base_url=base_url, token=token, api_version=api_version)

    @staticmethod
    def _compose_search_cql(space_key: str, cql: str, order_by: str | None = None) -> str:
        cql_text = cql.strip()
        extracted_order: str | None = None

        # Backward-compatible: if caller included ORDER BY inside cql, split it out.
        upper = cql_text.upper()
        idx = upper.rfind(" ORDER BY ")
        if idx >= 0:
            extracted_order = cql_text[idx + len(" ORDER BY "):].strip()
            cql_text = cql_text[:idx].strip()

        effective_order = (order_by or extracted_order or "").strip()
        base = f'space = {space_key} AND type = "page" AND ({cql_text})'
        if effective_order:
            return f"{base} ORDER BY {effective_order}"
        return base

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        params = params or {}
        cache_key = f"{self.api_version}|{self._token_cache_id}|{path}|{sorted(params.items())}"
        cached = API_CACHE.get(cache_key)
        if cached is not None:
            return cached

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0, verify=_http_verify_option()) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code in (401, 403):
            raise ConfluenceAuthError("Confluence authentication failed. Check X-Confluence-Token or CONFLUENCE_TOKEN.")
        resp.raise_for_status()
        payload = resp.json()
        API_CACHE.set(cache_key, payload)
        return payload

    @staticmethod
    def _cursor_to_start(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            return int(cursor)
        except ValueError:
            return 0

    async def search_space_cql(
        self,
        space_key: str,
        cql: str,
        limit: int,
        cursor: str | None,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        query_cql = self._compose_search_cql(space_key=space_key, cql=cql, order_by=order_by)
        if self.api_version == "v1":
            params: dict[str, Any] = {
                "cql": query_cql,
                "limit": limit,
                "start": self._cursor_to_start(cursor),
            }
            return await self._request("/rest/api/search", params=params)

        params = {
            "cql": query_cql,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return await self._request("/rest/api/search", params=params)

    async def get_page_tree_children(self, page_id: str, limit: int, cursor: str | None = None) -> dict[str, Any]:
        return await self.list_page_children(page_id=page_id, limit=limit, cursor=cursor)

    async def _get_content_v1(self, page_id: str, expand: str) -> dict[str, Any]:
        return await self._request(f"/rest/api/content/{page_id}", params={"expand": expand})

    @staticmethod
    def _normalize_v1_page(page: dict[str, Any], include_body: bool) -> dict[str, Any]:
        version = page.get("version") or {}
        author = version.get("by") or {}
        normalized: dict[str, Any] = {
            "id": page.get("id"),
            "title": page.get("title"),
            "version": {
                "number": version.get("number"),
                "createdAt": version.get("when"),
                "author": {"displayName": author.get("displayName")},
            },
        }
        if include_body:
            body_storage = ((page.get("body") or {}).get("storage") or {})
            normalized["body"] = {"storage": {"value": body_storage.get("value") or ""}}
        return normalized

    async def get_page_version(self, page_id: str) -> dict[str, Any]:
        if self.api_version == "v1":
            raw = await self._get_content_v1(page_id, expand="version")
            return self._normalize_v1_page(raw, include_body=False)
        params = {"include-version": "true"}
        return await self._request(f"/api/v2/pages/{page_id}", params=params)

    async def read_page_with_body(self, page_id: str) -> dict[str, Any]:
        if self.api_version == "v1":
            raw = await self._get_content_v1(page_id, expand="body.storage,version")
            return self._normalize_v1_page(raw, include_body=True)

        params = {
            "body-format": "storage",
            "include-version": "true",
            "include-operations": "false",
        }
        return await self._request(f"/api/v2/pages/{page_id}", params=params)

    async def list_page_children(self, page_id: str, limit: int, cursor: str | None) -> dict[str, Any]:
        if self.api_version == "v1":
            start = self._cursor_to_start(cursor)
            raw = await self._request(
                f"/rest/api/content/{page_id}/child/page",
                params={"limit": limit, "start": start},
            )
            links = raw.get("_links") or {}
            # normalize to cursor token-like form
            if links.get("next"):
                parsed = urlparse(str(links.get("next")))
                start_values = parse_qs(parsed.query).get("start")
                if start_values and start_values[0]:
                    links = {**links, "next": str(start_values[0])}
            return {
                "results": raw.get("results", []),
                "_links": links,
            }

        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(f"/api/v2/pages/{page_id}/children", params=params)

    async def get_page_ancestors(self, page_id: str) -> dict[str, Any]:
        if self.api_version == "v1":
            raw = await self._request(f"/rest/api/content/{page_id}", params={"expand": "ancestors"})
            ancestors = raw.get("ancestors") or []
            return {"results": [{"id": a.get("id"), "title": a.get("title")} for a in ancestors]}
        return await self._request(f"/api/v2/pages/{page_id}/ancestors")


def html_to_markdown(value: str | None) -> str:
    if not value:
        return ""
    return md(value, heading_style="ATX", escape_asterisks=False, escape_underscores=False)


def cache_dir() -> Path:
    path = Path(os.getenv("CONFLUENCE_CACHE_DIR", "/data/cache")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(page_id: str, version: str | int) -> Path:
    version_text = str(version)
    return cache_dir() / f"page_{page_id}_v{version_text}.md"
