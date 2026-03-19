from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

import httpx
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


API_CACHE = TTLRUCache(
    max_size=int(os.getenv("IN_MEMORY_CACHE_SIZE", "1000")),
    ttl_seconds=int(os.getenv("IN_MEMORY_CACHE_TTL_SECONDS", "1800")),
)


class ConfluenceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._token_cache_id = hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def from_token(cls, token: str) -> "ConfluenceClient":
        base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("CONFLUENCE_BASE_URL environment variable is required.")
        return cls(base_url=base_url, token=token)

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        params = params or {}
        cache_key = f"{self._token_cache_id}|{path}|{sorted(params.items())}"
        cached = API_CACHE.get(cache_key)
        if cached is not None:
            return cached

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code in (401, 403):
            raise ConfluenceAuthError("Confluence authentication failed. Check X-Confluence-Token or CONFLUENCE_TOKEN.")
        resp.raise_for_status()
        payload = resp.json()
        API_CACHE.set(cache_key, payload)
        return payload

    async def search_space_cql(self, space_key: str, cql: str, limit: int, cursor: str | None) -> dict[str, Any]:
        params = {
            "cql": f"space = {space_key} AND ({cql})",
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return await self._request("/rest/api/search", params=params)

    async def get_page_tree_children(self, page_id: str, limit: int, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(f"/api/v2/pages/{page_id}/children", params=params)

    async def get_page_version(self, page_id: str) -> dict[str, Any]:
        params = {"include-version": "true"}
        return await self._request(f"/api/v2/pages/{page_id}", params=params)

    async def read_page_with_body(self, page_id: str) -> dict[str, Any]:
        params = {
            "body-format": "storage",
            "include-version": "true",
            "include-operations": "false",
        }
        return await self._request(f"/api/v2/pages/{page_id}", params=params)

    async def list_page_children(self, page_id: str, limit: int, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(f"/api/v2/pages/{page_id}/children", params=params)

    async def get_page_ancestors(self, page_id: str) -> dict[str, Any]:
        return await self._request(f"/api/v2/pages/{page_id}/ancestors")


def html_to_markdown(value: str | None) -> str:
    if not value:
        return ""
    return md(value, heading_style="ATX")


def cache_dir() -> Path:
    path = Path(os.getenv("CONFLUENCE_CACHE_DIR", "/data/cache")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(page_id: str, version: str | int) -> Path:
    version_text = str(version)
    return cache_dir() / f"page_{page_id}_v{version_text}.md"
