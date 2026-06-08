# Stage 1: Builder - Red Hat Hardened Image (Project Hummingbird) with shell + dnf.
# Pinned to a digest for reproducibility; resolves to Python 3.12.13.
FROM registry.access.redhat.com/hi/python:3.12-builder@sha256:3d37bf07a9b663ac561e94dab30d771d0cb4a1dffbcd6aa4785af1d9b6bc5848 AS builder

# Build entirely as the image's non-root user (UID 65532). HOME is owned by that
# user, so no root escalation is needed. uv builds the venv directly from the
# lock file; the whole venv is copied into the distroless runtime stage.
ENV VENVS=${HOME}/.venvs
ENV PATH=${VENVS}/tools/bin:${PATH}
ENV UV_PROJECT=${HOME}/build
ENV UV_PROJECT_ENVIRONMENT=${VENVS}/okp-mcp
ENV UV_PYTHON=/usr/bin/python3

# Install uv (pinned) for fast, reproducible dependency resolution.
# Pinning keeps the locked build deterministic.
RUN python3 -m venv "${VENVS}/tools" \
    && "${VENVS}/tools/bin/python" -m pip install --no-cache-dir uv==0.11.14

# Copy dependency files first for layer caching.
COPY pyproject.toml uv.lock README.md ${UV_PROJECT}/
COPY src/ ${UV_PROJECT}/src/

# Build the venv straight from the lock file. `uv sync --locked` fails if
# uv.lock is stale, preserving the locked-build guarantee. No transient
# requirements file needed.
RUN uv venv --seed "${VENVS}/okp-mcp" \
    && uv sync --locked --no-cache --no-dev --no-editable

# Stage 2: Runtime - distroless Red Hat Hardened Image (no shell, no package manager).
FROM registry.access.redhat.com/hi/python:3.12@sha256:227cd08bc68a2fb2d79ed21d198c5dad0d130238feb4088881670296902c2754 AS runtime

LABEL com.redhat.component=okp-mcp
LABEL description="MCP server for the RHEL Offline Knowledge Portal"
LABEL name=okp-mcp
LABEL summary="OKP MCP Server"
LABEL vendor="Red Hat, Inc."

# Copy the dependency venv from the builder stage. It keeps the SAME path it was
# created at in the builder, so console-script shebangs (which bake an absolute
# interpreter path) stay valid without rewriting. All runtime dependencies are
# pure Python (no C/C++ extensions), so the distroless image needs no extra
# shared libraries.
COPY --from=builder ${HOME}/.venvs/okp-mcp ${HOME}/.venvs/okp-mcp

# License required by Red Hat preflight certification.
COPY LICENSE /licenses/LICENSE

# Put the venv on PATH so its console scripts and interpreter resolve first.
ENV PATH=${HOME}/.venvs/okp-mcp/bin:${PATH}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Bake the git commit SHA into the environment for build_info.py to read at
# runtime. Tekton passes this via --build-arg; defaults to "development" for
# local builds. Using an env var avoids writing/copying a file and the
# associated disk-read failure modes.
ARG COMMIT_SHA=development
ENV COMMIT_SHA=${COMMIT_SHA}

# Default to streamable-http for networked container deployments.
# Override with MCP_TRANSPORT=sse or MCP_TRANSPORT=stdio as needed.
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

# Distroless-safe liveness probe (no shell required): exec-form TCP connect to
# the listening port using the venv interpreter.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,socket,sys; p=int(os.getenv('MCP_PORT','8000')); s=socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1', p)) == 0 else 1)"]

# Run as the image's non-root user (UID 65532).
USER 65532

# Relative path: the runtime resolves this against PATH via execvp.
ENTRYPOINT ["okp-mcp"]
