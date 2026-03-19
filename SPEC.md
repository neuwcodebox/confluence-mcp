# Confluence MCP Server Spec

## Goals
Provide a compact, non-overlapping MCP toolset so AI can navigate Confluence wiki content efficiently.

## Development env loading
- On startup, server loads `.env` automatically (development convenience).
- Explicit process environment variables still take precedence.

## TLS verification
- Confluence HTTP client supports TLS verify control.
- `CONFLUENCE_SSL_VERIFY=false` disables certificate verification (use only when necessary).
- `CONFLUENCE_CA_BUNDLE` can point to a custom CA bundle path.

## API version mode
- `CONFLUENCE_API_VERSION=v2` (default): use v2 page/children/ancestor endpoints.
- `CONFLUENCE_API_VERSION=v1`: use legacy v1 content endpoints with normalized output.

## Authentication
1. **MCP server access auth**
   - Validate `Authorization` header against `MCP_AUTH_KEY` (if set).
2. **Confluence API auth**
   - Token priority: `X-Confluence-Token` header > `CONFLUENCE_TOKEN` env.
   - If both are missing, return an auth error.

## Caching
- Target: markdown converted page body (default dir `./cache` unless overridden).
- Cache key: `page_id + page_version`.
- Flow:
  1. Fetch page version metadata first.
  2. If cache exists for that version, return cached markdown.
  3. Else fetch body HTML, convert markdown, save cache.
  4. On cache miss, body is fetched and cached using the body response version to avoid cross-version race mismatch.

## In-memory API cache
- All Confluence API GET responses use an in-memory LRU+TTL cache.
- Default TTL: 1800 seconds (30 minutes).
- Default size: 1000 entries.
- Env overrides: `IN_MEMORY_CACHE_TTL_SECONDS`, `IN_MEMORY_CACHE_SIZE`.
- In-memory API cache key includes a SHA-256 hash-derived token identifier + request path + params (base URL excluded as runtime-constant).
- `.env` is loaded before in-memory cache initialization so `IN_MEMORY_CACHE_*` values are applied at startup.

## Pagination cursor normalization
- Parse `_links.next` and return pagination token in tool responses (`cursor` for v2, `start` for v1).
- Clients can pass returned token back to `cursor` directly.

## Content normalization
- Every tool returns structured JSON plus human-readable markdown text in unstructured content.
- Tool responses omit null/unused fields to reduce LLM token usage.
- Always convert Confluence HTML content to Markdown.
- Return reduced/curated JSON schema only.
- Prepend generated TOC to page read output.
- Apply truncation limit using `read_page.max_chars` or `MAX_MARKDOWN_CHARS`.

## Section-focused read (`read_page`)
- Optional `header_path` argument to return one section (`["Top", "Child", "Target"]`).
- If `header_path` has one name and duplicates exist, all matched sections are returned (each includes matched header path).
- For TOC-only skim, pass `header_path=["TOC"]`.
- Nested sub-headers are preserved as headings but body is collapsed:

```md
## Header 2
Body
### Header 3
(collapsed)
```

## CQL quick examples (for `search_space_cql.cql`)
- `type = "page"`
- `title ~ "release"`
- `text ~ "runbook"`
- `lastmodified >= "2024/01/01" ORDER BY lastmodified DESC`
- `creator = currentUser()`
- `label NOT IN (archived,obsolete)`
- `(type = "page" AND title ~ "api") OR (type = "blogpost" AND creator = currentUser())`

## Tool list
1. `search_space_cql`
   - Input: `cql`, `limit`, `cursor`
   - Behavior: caller controls full CQL filter (include `space = ...` explicitly when needed).
   - Output: page id/title/absolute url/excerpt (markdown), next cursor
2. `list_page_children`
   - Input: `page_id`, `limit`, `cursor`
   - Output: parent title + direct child pages
3. `read_page`
   - Input: `page_id`, `header_path?`, `max_chars?`
   - Output: title/body markdown(+TOC), version, cache hit, truncation info
4. `get_page_ancestors`
   - Input: `page_id`
   - Output: breadcrumb lineage

## Notes
- `list_spaces` removed (space key provided externally).
- `get_page_tree` removed and merged into `list_page_children` scope to avoid overlap.
