FROM python:3.11-slim AS base

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (without dev)
RUN uv sync --frozen --no-dev

# Copy source code
COPY src/ src/
COPY bin/ bin/

# Create data directory for sot.json files
RUN mkdir -p /data

# Default command: MCP server mode
# Override with: docker run kloc-intelligence import /data/sot.json
ENTRYPOINT ["uv", "run", "kloc-intelligence"]
CMD ["mcp-server"]
