FROM python:3.11-slim AS base

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock README.md ./

# Install dependencies (without dev) — include http (Starlette + uvicorn)
# and ai (Haystack + Qdrant) so both the MCP HTTP daemon and the kloc_search
# tool work out of the box.
RUN uv sync --frozen --no-dev --extra http --extra ai

# Copy source code
COPY src/ src/
COPY bin/ bin/

# Create data directory for sot.json files
RUN mkdir -p /data

EXPOSE 8765

# Default command: MCP server over Streamable HTTP, bound on all interfaces
# inside the container. The compose service maps this to 127.0.0.1 on the host.
# Override with: docker run kloc-intelligence import /data/sot.json
ENTRYPOINT ["uv", "run", "kloc-intelligence"]
CMD ["mcp-server-http", "--host", "0.0.0.0", "--port", "8765"]
