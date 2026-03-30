# Stage 1: Builder — UBI 10 full image has Python 3.12 + pip
FROM registry.access.redhat.com/ubi10:latest@sha256:c9c81d3d11bfd56c4bb61a220d4598392b5fbc500df33f2b8e52fdd2cebb4944 AS builder

WORKDIR /build

# Install pip then uv for fast, reproducible dependency resolution
RUN dnf install -y python3-pip && dnf clean all && python3 -m pip install uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Install production dependencies only (skip the project itself for now)
RUN uv sync --no-dev --no-install-project

# Cache the embedding model at build time using the venv's locked huggingface-hub
RUN /build/.venv/bin/python -c \
    "from huggingface_hub import snapshot_download; snapshot_download('ibm-granite/granite-embedding-30m-english', cache_dir='/build/models')"

# Copy source and install the package itself (no deps, already installed)
COPY src/ ./src/
RUN uv pip install . --no-deps && \
    sed -i 's|^#!.*python.*|#!/app/.venv/bin/python3|' /build/.venv/bin/okp-mcp

# Stage 2: Runtime — minimal UBI 10 Python 3.12 image
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest@sha256:3dc047bf30c6dac75b7a74aebcb8944ce35f46cc421543d9ce74716d2a6e611e

WORKDIR /app

LABEL com.redhat.component=okp-mcp
LABEL description="MCP server for the RHEL Offline Knowledge Portal"
LABEL name=okp-mcp
LABEL summary="OKP MCP Server"
LABEL vendor="Red Hat, Inc."

# Copy the virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy pre-cached embedding model from builder stage
COPY --from=builder /build/models /app/models

# License required by Red Hat preflight certification
COPY LICENSE /licenses/LICENSE

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Hugging Face Hub cache configuration for offline embedding model loading
ENV HF_HUB_CACHE=/app/models
ENV HF_HUB_OFFLINE=1

# Default to streamable-http for networked container deployments.
# Override with MCP_TRANSPORT=sse or MCP_TRANSPORT=stdio as needed.
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["okp-mcp"]
