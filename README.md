# confluence-mcp

An MCP server that helps AI agents navigate Confluence like a human: discover, search, follow hierarchy, and read pages safely within context limits.

## Quick start

```bash
uv sync
uv run confluence-mcp
```

## Environment variables

The server auto-loads a local `.env` file on startup (via `python-dotenv`) for development convenience.

- `CONFLUENCE_BASE_URL` (required): e.g. `https://your-domain.atlassian.net/wiki`
- `MCP_AUTH_KEY` (optional): server access key. If set, incoming `Authorization` header must match this value.
- `CONFLUENCE_TOKEN` (optional): default Confluence API token. `X-Confluence-Token` request header takes precedence.
- `CONFLUENCE_API_VERSION` (optional): `v2` (default) or `v1` for legacy Confluence instances.
- `CONFLUENCE_SSL_VERIFY` (optional): TLS cert verification toggle (`true` default, set `false` to disable).
- `CONFLUENCE_CA_BUNDLE` (optional): path to a custom CA bundle file for TLS verification.
- `CONFLUENCE_CACHE_DIR` (optional): markdown cache directory (default `/data/cache`)
- `MAX_MARKDOWN_CHARS` (optional): max markdown body size after conversion (default `12000`)
- `IN_MEMORY_CACHE_TTL_SECONDS` (optional): API response cache TTL in seconds (default `1800`)
- `IN_MEMORY_CACHE_SIZE` (optional): API response cache size (default `1000`)
- `MCP_TRANSPORT` (optional): `stdio` or `streamable-http` (Docker default: `streamable-http`)
- `MCP_HOST` (optional): host for HTTP mode (`127.0.0.1`, Docker default `0.0.0.0`)
- `MCP_PORT` (optional): port for HTTP mode (`8000`)

### .env support

Example `.env`:

```dotenv
CONFLUENCE_BASE_URL=https://your-domain.atlassian.net/wiki
MCP_AUTH_KEY=your-mcp-auth-key
CONFLUENCE_TOKEN=your-default-confluence-token
CONFLUENCE_API_VERSION=v2
MCP_TRANSPORT=streamable-http
MCP_HOST=0.0.0.0
MCP_PORT=8000
# if needed for self-signed/private CA environments
CONFLUENCE_SSL_VERIFY=false
# or: CONFLUENCE_CA_BUNDLE=/path/to/ca-bundle.pem
```

## Tooling

All tools include a human-readable `markdown` field (or markdown-formatted main content) for efficient browsing.

- `search_space_cql`: searches only page-type contents in a given `space_key` with CQL and returns absolute page URLs.
- `list_page_children`: returns direct children of a page and always includes parent title.
- `read_page`: returns markdown content (+TOC), cache metadata, truncation metadata, and optional section-focused view. `header_path` supports array-based hierarchical syntax (e.g., `["Top", "Child", "Target"]`). If a single name is duplicated, all matched sections are returned. For quick skim, set `header_path=["TOC"]`.
- `get_page_ancestors`: returns page breadcrumb lineage.


### search_space_cql quick examples

These go into the `cql` argument (the tool automatically adds `space = <space_key>`):

- `type = "page"`
- `title ~ "release"`
- `text ~ "runbook"`
- `lastmodified >= "2024/01/01" ORDER BY lastmodified DESC`
- `creator = currentUser()`
- `label NOT IN (archived,obsolete)`
- `(type = "page" AND title ~ "api") OR (type = "blogpost" AND creator = currentUser())`

## Caching and content shaping

- Uses in-memory LRU+TTL cache for Confluence API responses (default: 1000 entries, 1800s TTL).
- Reads page version first, then uses cache key `page_id + version`.
- Cache hit: returns cached markdown immediately.
- Cache miss: fetches page body, converts HTML to markdown, stores to disk cache.
- Adds a generated TOC at the beginning of output.
- Supports section-focused reading via `header_path` option.
- If content exceeds limit, truncates and appends `...(truncated)`.

## Docker

```bash
docker build -t confluence-mcp .
docker run --rm -p 8000:8000 \
  -e CONFLUENCE_BASE_URL="https://your-domain.atlassian.net/wiki" \
  -e MCP_AUTH_KEY="your-mcp-auth-key" \
  -e CONFLUENCE_TOKEN="your-default-confluence-token" \
  -e MCP_TRANSPORT="streamable-http" \
  -e MCP_HOST="0.0.0.0" \
  -e MCP_PORT="8000" \
  -v confluence-mcp-cache:/data/cache \
  confluence-mcp
```
