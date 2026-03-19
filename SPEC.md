# Confluence MCP Server Spec

## Goals
Provide a compact, non-overlapping MCP toolset so AI can navigate Confluence wiki content efficiently.

## Authentication
1. **MCP server access auth**
   - Validate `Authorization` header against `MCP_AUTH_KEY` (if set).
2. **Confluence API auth**
   - Token priority: `X-Confluence-Token` header > `CONFLUENCE_TOKEN` env.
   - If both are missing, return an auth error.

## Caching
- Target: markdown converted page body.
- Cache key: `page_id + page_version`.
- Flow:
  1. Fetch page version metadata first.
  2. If cache exists for that version, return cached markdown.
  3. Else fetch body HTML, convert markdown, save cache.

## In-memory API cache
- All Confluence API GET responses use an in-memory LRU+TTL cache.
- Default TTL: 1800 seconds (30 minutes).
- Default size: 1000 entries.
- Env overrides: `IN_MEMORY_CACHE_TTL_SECONDS`, `IN_MEMORY_CACHE_SIZE`.

## Content normalization
- Always convert Confluence HTML content to Markdown.
- Return reduced/curated JSON schema only.
- Prepend generated TOC to page read output.
- Apply truncation limit using `read_page.max_chars` or `MAX_MARKDOWN_CHARS`.

## Section-focused read (`read_page`)
- Optional `header` argument to return one section.
- Nested sub-headers are preserved as headings but body is collapsed:

```md
## Header 2
Body
### Header 3
(collapsed)
```

## CQL assistance
- Provide a dedicated tool (`get_cql_examples`) with practical CQL examples.
- Source reference: Atlassian CQL guide.
  - https://developer.atlassian.com/server/confluence/advanced-searching-using-cql/

## Tool list
1. `get_cql_examples`
   - Output: docs URL + example CQL templates.
2. `search_space_cql`
   - Input: `space_key`, `cql`, `limit`, `cursor`
   - Output: page id/title/url/excerpt (markdown), next cursor
3. `list_page_children`
   - Input: `page_id`, `limit`, `cursor`
   - Output: parent title + direct child pages
4. `read_page`
   - Input: `page_id`, `header?`, `max_chars?`
   - Output: title/body markdown(+TOC), version, cache hit, truncation info
5. `get_page_ancestors`
   - Input: `page_id`
   - Output: breadcrumb lineage

## Notes
- `list_spaces` removed (space key provided externally).
- `get_page_tree` removed and merged into `list_page_children` scope to avoid overlap.
