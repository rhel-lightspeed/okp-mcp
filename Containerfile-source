# Stage 1: Builder - Red Hat Hardened Image (Project Hummingbird) with shell + dnf.
# Pinned to a digest for reproducibility; resolves to Python 3.12.13.
FROM registry.access.redhat.com/hi/python:3.12-builder@sha256:3d37bf07a9b663ac561e94dab30d771d0cb4a1dffbcd6aa4785af1d9b6bc5848 AS builder

# Build entirely as the image's non-root user (UID 65532). HOME is owned by that
# user, so no root escalation is needed. The whole app venv is copied into the
# distroless runtime stage.
ENV VENVS=${HOME}/.venvs
ENV PATH=${VENVS}/tools/bin:${PATH}
ENV UV_PROJECT=${HOME}/build
ENV UV_PROJECT_ENVIRONMENT=${VENVS}/okp-mcp
ENV UV_PYTHON=/usr/bin/python3

# Copy dependency files first for layer caching. .konflux holds the
# hash-pinned manifests Cachi2 prefetches for hermetic builds; they are
# generated from uv.lock by scripts/konflux_requirements.sh.
COPY pyproject.toml uv.lock README.md ${UV_PROJECT}/
COPY .konflux/ ${UV_PROJECT}/.konflux/
COPY src/ ${UV_PROJECT}/src/

WORKDIR ${UV_PROJECT}

# Product Security requirement: build all wheels from source instead of manylinux.
# Requires root to install C/Rust toolchains before dropping back to non-root.
USER root
RUN dnf install -y gcc gcc-c++ python3.12-devel openssl-devel rust cargo pkg-config libffi-devel \
    && dnf clean all
USER 65532

# Install into a venv at a fixed path so the distroless runtime can copy it
# whole. Two install paths, one app-venv location:
#
#   * Hermetic Konflux build (Cachi2 present): the network is disabled, so uv
#     cannot be fetched from PyPI. Cachi2 has prefetched every wheel into an
#     offline mirror and written /cachi2/cachi2.env (PIP_FIND_LINKS +
#     PIP_NO_INDEX). Two stdlib venvs keep the build backend off the runtime:
#       1. A throwaway tools venv gets the hash-pinned hatchling backend and
#          builds the okp_mcp wheel with --no-build-isolation.
#       2. The app venv gets only the hash-pinned runtime deps, then the
#          locally built okp_mcp wheel (--no-deps --no-index). hatchling and
#          its build deps never touch the app venv, so nothing needs
#          uninstalling and the runtime SBOM carries no build tooling. This
#          also dodges a version clash: packaging is both a runtime dep and a
#          hatchling build dep, and mixing both manifests in one venv would
#          conflict.
#   * Local / non-hermetic build: install pinned uv, then `uv sync --locked`
#     straight from uv.lock (fails if the lock is stale, preserving the
#     locked-build guarantee). `uv venv --seed` seeds pip into the app venv
#     (just pip on 3.12+); uv sync does not need it, but it keeps the local
#     venv at parity with the hermetic path's `python -m venv` (which also
#     ships pip).
#
# uv.lock stays the single source of truth in both paths: .konflux/requirements*.txt
# are generated from it, never hand-edited.
RUN if [ -f /cachi2/cachi2.env ]; then \
        . /cachi2/cachi2.env \
        && python3 -m venv "${VENVS}/build" \
        && "${VENVS}/build/bin/pip" install --no-cache-dir --no-binary=:all: --require-hashes \
            -r .konflux/requirements-build.txt \
        && "${VENVS}/build/bin/pip" wheel --no-cache-dir --no-build-isolation --no-deps . -w "${HOME}/wheels" \
        && python3 -m venv "${UV_PROJECT_ENVIRONMENT}" \
        && "${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir --no-binary=:all: --require-hashes \
            -r .konflux/requirements.txt \
        && "${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir --no-deps --no-index \
            --find-links "${HOME}/wheels" okp_mcp \
        && "${UV_PROJECT_ENVIRONMENT}/bin/python" -c "import okp_mcp"; \
    else \
        python3 -m venv "${VENVS}/tools" \
        && "${VENVS}/tools/bin/python" -m pip install --no-cache-dir uv==0.11.14 \
        && uv venv --seed "${UV_PROJECT_ENVIRONMENT}" \
        && uv sync --locked --no-cache --no-dev --no-editable --no-binary; \
    fi

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
