# Stage 1: Builder — UBI 10 full image has Python 3.12 + pip
FROM registry.access.redhat.com/ubi10:latest@sha256:387dc083b03817a603f76da19e017542afcef94d25c2a7d6fc4fd43af9d81fe7 AS builder

WORKDIR /build

# Install pip then uv for fast, reproducible dependency resolution
RUN dnf install -y python3-pip && dnf clean all && python3 -m pip install uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Install production dependencies only (skip the project itself for now)
RUN uv sync --no-dev --no-install-project

# Copy source and install the package itself (no deps, already installed)
COPY src/ ./src/
RUN uv pip install . --no-deps && \
    sed -i 's|^#!.*python.*|#!/app/.venv/bin/python3|' /build/.venv/bin/okp-mcp

# Stage 2: Runtime — minimal UBI 10 Python 3.12 image
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest@sha256:874836534b6220e4cbe697d1e622dbee9fd1804d882cec5f027ca8d3e583d179

WORKDIR /app

LABEL com.redhat.component=okp-mcp
LABEL description="MCP server for the RHEL Offline Knowledge Portal"
LABEL name=okp-mcp
LABEL summary="OKP MCP Server"
LABEL vendor="Red Hat, Inc."

# Copy the virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# License required by Red Hat preflight certification
COPY LICENSE /licenses/LICENSE

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default to streamable-http for networked container deployments.
# Override with MCP_TRANSPORT=sse or MCP_TRANSPORT=stdio as needed.
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["okp-mcp"]
