from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from dotenv import load_dotenv

from confluence_mcp.confluence import (
    ConfluenceAuthError,
    ConfluenceClient,
    MCPAuthorizationError,
    cache_path,
    html_to_markdown,
)
from confluence_mcp.models import (
    AncestorItem,
    AncestorResult,
    ChildPageListResult,
    PageContent,
    PageSummary,
    SearchResult,
)

load_dotenv()

mcp = FastMCP(
    "confluence-mcp",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _header_map(ctx: Context | None) -> dict[str, str]:
    if ctx is None:
        return {}
    req_ctx = getattr(ctx, "request_context", None)
    if req_ctx is None:
        return {}

    headers: dict[str, str] = {}

    # FastMCP streamable-http path: ctx.request_context.request.headers
    request = getattr(req_ctx, "request", None)
    if request is not None:
        req_headers = getattr(request, "headers", None)
        if req_headers is not None:
            try:
                for k, v in req_headers.items():
                    if isinstance(v, str):
                        headers[str(k).lower()] = v
            except Exception:
                pass

    # Compatibility fallbacks for non-http/meta transports
    for attr in ("headers", "meta", "metadata"):
        obj = getattr(req_ctx, attr, None)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str):
                    headers[str(k).lower()] = v

    return headers


def _validate_mcp_auth(ctx: Context | None) -> None:
    expected = os.getenv("MCP_AUTH_KEY", "").strip()
    if not expected:
        return

    headers = _header_map(ctx)
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        provided = auth.split(" ", 1)[1].strip()
    else:
        provided = auth.strip()

    if provided != expected:
        raise MCPAuthorizationError("MCP Authorization failed: invalid Authorization header key.")


def _confluence_token(ctx: Context | None) -> str:
    headers = _header_map(ctx)
    token = headers.get("x-confluence-token", "").strip()
    if token:
        return token

    env_token = os.getenv("CONFLUENCE_TOKEN", "").strip()
    if env_token:
        return env_token
    raise ConfluenceAuthError("Confluence token missing. Provide X-Confluence-Token header or CONFLUENCE_TOKEN.")


def _client_from_context(ctx: Context | None) -> ConfluenceClient:
    _validate_mcp_auth(ctx)
    return ConfluenceClient.from_token(_confluence_token(ctx))


def _next_cursor(payload: dict[str, Any]) -> str | None:
    links = payload.get("_links") or {}
    raw_next = links.get("next")
    if not isinstance(raw_next, str) or not raw_next:
        return None

    parsed = urlparse(raw_next)
    query = parse_qs(parsed.query)
    cursor_values = query.get("cursor")
    if cursor_values and cursor_values[0]:
        return cursor_values[0]

    # fallback: when API already returns plain token
    if "?" not in raw_next and "&" not in raw_next and "/" not in raw_next:
        return raw_next
    return None


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _extract_headings(markdown_text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line in markdown_text.splitlines():
        matched = HEADING_RE.match(line)
        if matched:
            headings.append((len(matched.group(1)), matched.group(2).strip()))
    return headings


def _build_toc(markdown_text: str) -> str:
    headings = _extract_headings(markdown_text)
    if not headings:
        return ""

    lines = ["## Table of Contents"]
    for level, title in headings:
        indent = "  " * (level - 1)
        lines.append(f"{indent}- {title}")
    return "\n".join(lines)


def _extract_section(markdown_text: str, header: str) -> str:
    lines = markdown_text.splitlines()
    target_index = -1
    target_level = 0
    target_name = _normalize_heading(header)

    for idx, line in enumerate(lines):
        matched = HEADING_RE.match(line)
        if not matched:
            continue
        level = len(matched.group(1))
        title = _normalize_heading(matched.group(2))
        if title == target_name:
            target_index = idx
            target_level = level
            break

    if target_index < 0:
        raise ValueError(f"Requested header not found: {header}")

    out = [lines[target_index]]
    in_nested_subsection = False
    for idx in range(target_index + 1, len(lines)):
        line = lines[idx]
        matched = HEADING_RE.match(line)
        if matched:
            level = len(matched.group(1))
            if level <= target_level:
                break

            out.append(line)
            out.append("(collapsed)")
            in_nested_subsection = True
            continue

        if not in_nested_subsection:
            out.append(line)

    return "\n".join(out).strip()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    clipped = text[:limit].rstrip()
    return f"{clipped}\n\n...(truncated)", True


@mcp.tool()
async def search_space_cql(space_key: str, cql: str, limit: int = 10, cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """Run CQL search in a specific space.

    Quick CQL recipes (pass only this right-hand CQL expression; this tool prepends `space = <space_key>`):

    1) All pages
       type = "page"

    2) Title contains keyword
       title ~ "release"

    3) Full-text contains term
       text ~ "runbook"

    4) Recently updated first
       lastmodified >= "2024/01/01" ORDER BY lastmodified DESC

    5) Created by current user
       creator = currentUser()

    6) Exclude labels
       label NOT IN (archived,obsolete)

    7) Boolean + parentheses precedence
       (type = "page" AND title ~ "api") OR (type = "blogpost" AND creator = currentUser())

    8) Date function + label filter
       lastmodified < startOfYear() OR label = needs_review

    9) Ordered list with tie-breaker
       type = "page" ORDER BY created DESC, title ASC

    Tips:
    - Use double quotes for phrases/date strings.
    - Prefer IN / NOT IN for multi-value filters.
    - Combine conditions with AND/OR and parentheses explicitly.
    """
    client = _client_from_context(ctx)
    data = await client.search_space_cql(space_key=space_key, cql=cql, limit=limit, cursor=cursor)

    items: list[PageSummary] = []
    for row in data.get("results", []):
        content = row.get("content") or {}
        excerpt_html = row.get("excerpt") or ""
        items.append(
            PageSummary(
                page_id=str(content.get("id", "")),
                title=content.get("title") or "(untitled)",
                url=((content.get("_links") or {}).get("webui")),
                excerpt_markdown=html_to_markdown(excerpt_html),
            )
        )
    result = SearchResult(items=items, next_cursor=_next_cursor(data))
    return result.model_dump()


@mcp.tool()
async def read_page(page_id: str, header: str | None = None, max_chars: int | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """Read a page as Markdown. Optionally provide a header to return a focused section only."""
    client = _client_from_context(ctx)

    version_data = await client.get_page_version(page_id)
    version_no = (version_data.get("version") or {}).get("number")
    cache_file = cache_path(page_id, version_no if version_no is not None else "unknown")

    cache_hit = False
    if cache_file.exists():
        raw_markdown = cache_file.read_text(encoding="utf-8")
        page_data = version_data
        cache_hit = True
    else:
        page_data = await client.read_page_with_body(page_id)
        body_html = ((page_data.get("body") or {}).get("storage") or {}).get("value") or ""
        raw_markdown = html_to_markdown(body_html)
        cache_file.write_text(raw_markdown, encoding="utf-8")

    toc = _build_toc(raw_markdown)
    selected = _extract_section(raw_markdown, header) if header else raw_markdown

    limit = max_chars or int(os.getenv("MAX_MARKDOWN_CHARS", "12000"))
    truncated_body, truncated = _truncate(selected, limit)

    final_body = f"{toc}\n\n{truncated_body}" if toc else truncated_body

    result = PageContent(
        page_id=str(page_data.get("id", page_id)),
        title=page_data.get("title") or "(untitled)",
        version=str(version_no) if version_no is not None else None,
        body_markdown=final_body,
        toc_markdown=toc or None,
        section=header,
        truncated=truncated,
        cache_hit=cache_hit,
        last_modified=((page_data.get("version") or {}).get("createdAt")),
        author=(((page_data.get("version") or {}).get("author") or {}).get("displayName")),
    )
    return result.model_dump()


@mcp.tool()
async def list_page_children(page_id: str, limit: int = 50, cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """List direct children of a page with parent title included."""
    client = _client_from_context(ctx)
    data = await client.list_page_children(page_id=page_id, limit=limit, cursor=cursor)

    parent_data = await client.get_page_version(page_id)
    parent_title = parent_data.get("title")

    items = [
        PageSummary(page_id=str(c.get("id", "")), title=c.get("title", "(untitled)"), url=None, excerpt_markdown=None)
        for c in data.get("results", [])
    ]
    return ChildPageListResult(
        parent_page_id=page_id,
        parent_title=parent_title,
        items=items,
        next_cursor=_next_cursor(data),
    ).model_dump()


@mcp.tool()
async def get_page_ancestors(page_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Get breadcrumb ancestors for a page."""
    client = _client_from_context(ctx)
    data = await client.get_page_ancestors(page_id)

    breadcrumb = [AncestorItem(page_id=str(a.get("id", "")), title=a.get("title", "(untitled)")) for a in data.get("results", [])]
    return AncestorResult(page_id=page_id, breadcrumb=breadcrumb).model_dump()


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
