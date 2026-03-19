from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse, urljoin
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
    ChildPageItem,
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

    # v1 fallback: start-offset based pagination
    start_values = query.get("start")
    if start_values and start_values[0]:
        return start_values[0]

    # fallback: when API already returns plain token
    if "?" not in raw_next and "&" not in raw_next and "/" not in raw_next:
        return raw_next
    return None




def _absolute_webui_url(base_url: str, maybe_relative: str | None) -> str | None:
    if not maybe_relative:
        return None
    if maybe_relative.startswith("http://") or maybe_relative.startswith("https://"):
        return maybe_relative
    return urljoin(base_url.rstrip("/") + "/", maybe_relative)

def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()




def _collect_heading_entries(markdown_text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    stack: list[str] = []

    for idx, line in enumerate(markdown_text.splitlines()):
        matched = HEADING_RE.match(line)
        if not matched:
            continue

        level = len(matched.group(1))
        title = matched.group(2).strip()

        # keep stack depth aligned to heading level
        while len(stack) >= level:
            stack.pop()
        stack.append(title)

        entries.append(
            {
                "index": idx,
                "level": level,
                "title": title,
                "path": " > ".join(stack),
            }
        )

    return entries

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


def _extract_section(markdown_text: str, header_path: list[str]) -> str:
    lines = markdown_text.splitlines()
    heading_entries = _collect_heading_entries(markdown_text)
    normalized_parts = [_normalize_heading(part) for part in header_path if part.strip()]
    if not normalized_parts:
        raise ValueError("header_path must contain at least one non-empty heading name.")

    if len(normalized_parts) == 1:
        target_norm = normalized_parts[0]
        matches = [e for e in heading_entries if _normalize_heading(e["title"]) == target_norm]
    else:
        def _entry_path_parts(entry: dict[str, Any]) -> list[str]:
            return [_normalize_heading(part) for part in str(entry["path"]).split(" > ")]
        matches = [e for e in heading_entries if _entry_path_parts(e) == normalized_parts]

    if not matches:
        raise ValueError(f"Requested header_path not found: {header_path}")

    # Return all matches (duplicate titles or duplicate full paths).
    targets = matches

    sections: list[str] = []
    for target in targets:
        target_index = int(target["index"])
        target_level = int(target["level"])

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

        section_text = "\n".join(out).strip()
        if len(targets) > 1:
            section_text = f"### Matched Header Path: {target['path']}\n\n{section_text}"
        sections.append(section_text)

    return "\n\n---\n\n".join(sections)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    clipped = text[:limit].rstrip()
    return f"{clipped}\n\n...(truncated)", True


def _format_page_markdown(
    title: str,
    version: str | None,
    last_modified: str | None,
    section_path: list[str] | None,
    toc: str | None,
    content: str,
) -> str:
    section_repr = " > ".join(section_path) if section_path else ""
    yaml_header = [
        "---",
        f"title: {title}",
        f"version: {version or ''}",
        f"last_modified: {last_modified or ''}",
        f"section_path: {section_repr}",
        "---",
    ]
    body_parts: list[str] = []
    if toc:
        body_parts.append("## TOC")
        body_parts.append(toc)
    body_parts.append("## Content")
    body_parts.append(content)
    return "\n".join(yaml_header + [""] + body_parts).strip()


@mcp.tool()
async def search_space_cql(space_key: str, cql: str, limit: int = 10, cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """Search entry-point for wiki exploration.

    Recommended flow:
    - Find candidate pages first (id/title/url),
    - Inspect content with `read_page`,
    - Expand local/global context with `list_page_children` and `get_page_ancestors`.

    Quick CQL recipes (pass only this right-hand CQL expression; this tool prepends `space = <space_key> AND type = "page"`):

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
                url=_absolute_webui_url(client.base_url, ((content.get("_links") or {}).get("webui"))),
                excerpt_markdown=html_to_markdown(excerpt_html),
            )
        )
    result = SearchResult(items=items, next_cursor=_next_cursor(data))
    payload = result.model_dump(exclude_none=True)
    bullet_lines = [f"- `{item.page_id}` [{item.title}]({item.url})" if item.url else f"- `{item.page_id}` {item.title}" for item in items]
    payload["markdown"] = "\n".join(["## Search Results", *bullet_lines]) if bullet_lines else "## Search Results\n- (empty)"
    return payload


@mcp.tool()
async def read_page(
    page_id: str,
    header_path: list[str] | None = None,
    max_chars: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Read a page as markdown for understanding/summarization.

    Recommended usage:
    - Quick skim: `header_path=["Table of Contents"]`
    - Focused read: `header_path=["Parent", "Child"]`
    - If duplicated headings match, all matching sections are returned.
    """
    client = _client_from_context(ctx)

    version_data = await client.get_page_version(page_id)
    version_no = (version_data.get("version") or {}).get("number")
    initial_cache_file = cache_path(page_id, version_no if version_no is not None else "unknown")

    cache_hit = False
    if initial_cache_file.exists():
        raw_markdown = initial_cache_file.read_text(encoding="utf-8")
        page_data = version_data
        cache_hit = True
    else:
        page_data = await client.read_page_with_body(page_id)
        body_version_no = (page_data.get("version") or {}).get("number")
        version_no = body_version_no if body_version_no is not None else version_no

        final_cache_file = cache_path(page_id, version_no if version_no is not None else "unknown")
        if final_cache_file.exists():
            raw_markdown = final_cache_file.read_text(encoding="utf-8")
            cache_hit = True
        else:
            body_html = ((page_data.get("body") or {}).get("storage") or {}).get("value") or ""
            raw_markdown = html_to_markdown(body_html)
            final_cache_file.write_text(raw_markdown, encoding="utf-8")

    toc = _build_toc(raw_markdown)
    toc_path_requested = bool(header_path) and len(header_path) == 1 and _normalize_heading(header_path[0]) == _normalize_heading("Table of Contents")
    selected = toc if toc_path_requested else (_extract_section(raw_markdown, header_path) if header_path else raw_markdown)

    limit = max_chars or int(os.getenv("MAX_MARKDOWN_CHARS", "12000"))
    truncated_body, truncated = _truncate(selected, limit)
    final_body = _format_page_markdown(
        title=page_data.get("title") or "(untitled)",
        version=str(version_no) if version_no is not None else None,
        last_modified=((page_data.get("version") or {}).get("createdAt")),
        section_path=header_path,
        toc=toc or None,
        content=truncated_body,
    )

    result = PageContent(
        page_id=str(page_data.get("id", page_id)),
        title=page_data.get("title") or "(untitled)",
        version=str(version_no) if version_no is not None else None,
        body_markdown=final_body,
        toc_markdown=toc or None,
        section=" > ".join(header_path) if header_path else None,
        truncated=truncated,
        cache_hit=cache_hit,
        last_modified=((page_data.get("version") or {}).get("createdAt")),
    )
    return result.model_dump(exclude_none=True)


@mcp.tool()
async def list_page_children(page_id: str, limit: int = 50, cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """List direct child pages for local navigation context."""
    client = _client_from_context(ctx)
    data = await client.list_page_children(page_id=page_id, limit=limit, cursor=cursor)

    parent_data = await client.get_page_version(page_id)
    parent_title = parent_data.get("title")

    items = [
        ChildPageItem(page_id=str(c.get("id", "")), title=c.get("title", "(untitled)"))
        for c in data.get("results", [])
    ]
    payload = ChildPageListResult(
        parent_page_id=page_id,
        parent_title=parent_title,
        items=items,
        next_cursor=_next_cursor(data),
    ).model_dump(exclude_none=True)
    child_lines = [f"- `{item.page_id}` {item.title}" for item in items]
    payload["markdown"] = "\n".join([f"## Children of {parent_title or page_id}", *child_lines]) if child_lines else f"## Children of {parent_title or page_id}\n- (empty)"
    return payload


@mcp.tool()
async def get_page_ancestors(page_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Get breadcrumb-style ancestor path for global context."""
    client = _client_from_context(ctx)
    data = await client.get_page_ancestors(page_id)

    breadcrumb = [AncestorItem(page_id=str(a.get("id", "")), title=a.get("title", "(untitled)")) for a in data.get("results", [])]
    payload = AncestorResult(page_id=page_id, breadcrumb=breadcrumb).model_dump(exclude_none=True)
    crumb = " > ".join([item.title for item in breadcrumb]) if breadcrumb else "(no ancestors)"
    payload["markdown"] = f"## Ancestor Path\n{crumb}"
    return payload


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
