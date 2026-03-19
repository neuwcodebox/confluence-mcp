FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml README.md ./
COPY uv.lock ./
COPY src ./src

RUN uv sync --frozen --no-dev

ENV CONFLUENCE_BASE_URL="" \
    CONFLUENCE_CACHE_DIR=/data/cache \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000
VOLUME ["/data/cache"]

CMD ["uv", "run", "confluence-mcp"]
