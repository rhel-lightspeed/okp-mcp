# Common base image
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest@sha256:d8976587dd9ac477abb8c34148f06c9f7cb66f4740c30ef1cac92f5037403e89 AS base

# Stage 1: Builder
FROM base AS builder

WORKDIR /build

# Install uv for fast, reproducible dependency resolution
RUN python3 -m venv tools && tools/bin/pip install uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Specify uv project, virtual environment, and Python executable
ENV UV_PROJECT=/build
ENV UV_PROJECT_ENVIRONMENT="${APP_ROOT}"
ENV UV_PYTHON=/usr/bin/python3

# Copy source and install the package
COPY src/ ./src/
RUN tools/bin/uv sync --no-cache --locked --no-dev --no-editable

# Stage 2: Runtime - minimal UBI 10 Python 3.12 image
FROM base

WORKDIR "${APP_ROOT}"

LABEL com.redhat.component=okp-mcp
LABEL description="MCP server for the RHEL Offline Knowledge Portal"
LABEL name=okp-mcp
LABEL summary="OKP MCP Server"
LABEL vendor="Red Hat, Inc."

# Copy the virtual environment from builder
COPY --from=builder "$APP_ROOT" "$APP_ROOT"

# License required by Red Hat preflight certification
COPY LICENSE /licenses/LICENSE

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Bake the git commit SHA into the image at build time.
# Tekton passes this via --build-arg; defaults to "development" for local builds.
ARG COMMIT_SHA=development
RUN printf '%s\n' "${COMMIT_SHA}" > "${APP_ROOT}/COMMIT_SHA"

# Default to streamable-http for networked container deployments.
# Override with MCP_TRANSPORT=sse or MCP_TRANSPORT=stdio as needed.
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["okp-mcp"]
