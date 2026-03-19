# confluence-mcp

An MCP server that helps AI agents navigate Confluence like a human: discover, search, follow hierarchy, and read pages safely within context limits.

## Quick start

```bash
uv sync
uv run confluence-mcp
```

## Environment variables

- `CONFLUENCE_BASE_URL` (required): e.g. `https://your-domain.atlassian.net/wiki`
- `MCP_AUTH_KEY` (optional): server access key. If set, incoming `Authorization` header must match this value.
- `CONFLUENCE_TOKEN` (optional): default Confluence API token. `X-Confluence-Token` request header takes precedence.
- `CONFLUENCE_CACHE_DIR` (optional): markdown cache directory (default `/data/cache`)
- `MAX_MARKDOWN_CHARS` (optional): max markdown body size after conversion (default `12000`)
- `IN_MEMORY_CACHE_TTL_SECONDS` (optional): API response cache TTL in seconds (default `1800`)
- `IN_MEMORY_CACHE_SIZE` (optional): API response cache size (default `1000`)
- `MCP_TRANSPORT` (optional): `stdio` (default) or `streamable-http`
- `MCP_HOST` (optional): host for HTTP mode (`127.0.0.1`)
- `MCP_PORT` (optional): port for HTTP mode (`8000`)

## Tooling

- `search_space_cql`: searches pages in a given `space_key` with CQL (includes CQL examples in tool description).
- `list_page_children`: returns direct children of a page and always includes parent title.
- `read_page`: returns markdown content (+TOC), cache metadata, truncation metadata, and optional section-focused view.
- `get_page_ancestors`: returns page breadcrumb lineage.

## Caching and content shaping

- Uses in-memory LRU+TTL cache for Confluence API responses (default: 1000 entries, 1800s TTL).
- Reads page version first, then uses cache key `page_id + version`.
- Cache hit: returns cached markdown immediately.
- Cache miss: fetches page body, converts HTML to markdown, stores to disk cache.
- Adds a generated TOC at the beginning of output.
- Supports section-focused reading via `header` option.
- If content exceeds limit, truncates and appends `...(truncated)`.

## Docker

```bash
docker build -t confluence-mcp .
docker run --rm \
  -e CONFLUENCE_BASE_URL="https://your-domain.atlassian.net/wiki" \
  -e MCP_AUTH_KEY="your-mcp-auth-key" \
  -e CONFLUENCE_TOKEN="your-default-confluence-token" \
  -v confluence-mcp-cache:/data/cache \
  confluence-mcp
```
