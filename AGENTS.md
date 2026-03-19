# AGENTS.md

## Working rules
- Use **uv** for dependency management and execution.
- Keep Python >= 3.11.
- Implement server with MCP Python SDK (`mcp`).
- Use `Authorization` only for MCP access key (`MCP_AUTH_KEY`) validation.
- Use `X-Confluence-Token` first for Confluence auth, then `CONFLUENCE_TOKEN` fallback.
- Convert Confluence HTML to Markdown before returning tool results.
- Cache markdown body to `CONFLUENCE_CACHE_DIR` with `page_id+version` cache key.
- Add TOC and truncation (`MAX_MARKDOWN_CHARS`) for large page content.
- Maintain in-memory cache defaults: TTL 1800s, size 1000 (unless overridden by env).
- Keep toolset compact and non-overlapping.
- Include and maintain a CQL example tool so AI clients can bootstrap CQL usage.
- Apply in-memory LRU+TTL cache to API responses (default TTL 30 minutes, size 1000).

## Maintenance checklist
- Run `uv sync` when dependencies change.
- Update SPEC.md when tools/signatures/policies change.
- Keep README in English for global users.
- Ensure Docker cache volume path is documented and still correct.
