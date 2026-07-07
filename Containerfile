# Stage 1: Builder - Hummingbird Python 3.12 builder (has bash, dnf5, pip).
FROM registry.access.redhat.com/hi/python:3.13-builder@sha256:fdda91a556ecd99a9886f5e0d3b9a379ec9835ec162a940364bef41b39a552bd AS builder

# Builder defaults to non-root (UID 65532); root is needed to create /opt and
# install packages. This stage is ephemeral — only the venv is copied out.
USER 0

ENV VENVS=/opt/.venvs
ENV UV_PROJECT=/build
ENV UV_PROJECT_ENVIRONMENT=${VENVS}/okp-mcp
ENV UV_PYTHON=/usr/bin/python3.12

# Build from source by default (Product Security requirement for Konflux).
# Override with --build-arg BUILD_FROM_SOURCE=0 for fast prebuilt-wheel builds.
ARG BUILD_FROM_SOURCE=1

# Copy dependency files first for layer caching. .konflux holds the hash-pinned
# Python manifests Hermeto prefetches for hermetic builds; they are generated
# from uv.lock by scripts/konflux_requirements.py. rpms.lock.yaml pins the
# build-toolchain RPMs Hermeto prefetches (see rpms.in.yaml).
COPY pyproject.toml uv.lock README.md rpms.lock.yaml ${UV_PROJECT}/
COPY .konflux/ ${UV_PROJECT}/.konflux/
COPY src/ ${UV_PROJECT}/src/
COPY scripts/container-install.sh scripts/install-toolchain.sh ${UV_PROJECT}/scripts/

WORKDIR ${UV_PROJECT}

# Set BUILD_FROM_SOURCE as an env var so both scripts can read it.
ENV BUILD_FROM_SOURCE=${BUILD_FROM_SOURCE}

# Install the C/Rust build toolchain. The script exits early when
# BUILD_FROM_SOURCE!=1, so prebuilt-wheel builds skip the toolchain.
RUN scripts/install-toolchain.sh

# Install dependencies via the shared build script.
# See scripts/container-install.sh for detailed comments on each step.
RUN scripts/container-install.sh

# Stage 2: Runtime - Hummingbird Python 3.12 distroless.
FROM registry.access.redhat.com/hi/python:3.14@sha256:89c156cdc3be9d43befcfcda94800d8531ae9294fc8e5d0033be957d555afa8e AS runtime

LABEL com.redhat.application=rhel-knowledge-bridge
LABEL com.redhat.component=rhel-knowledge-bridge
LABEL description="Red Hat Offline Knowledge Portal"
LABEL distribution-scope=private
LABEL io.k8s.description="Red Hat Offline Knowledge Portal"
LABEL io.k8s.display-name="RHEL Offline Knowledge Portal MCP server"
LABEL io.openshift.tags="rhel,knowledge-portal,mcp"
LABEL name="rhel-cla/rhel-knowledge-bridge-rhel10"
LABEL cpe="cpe:/a:redhat:rhel_cla:1.0::el10"
LABEL release="1.0"
LABEL version=1.0
LABEL url="https://github.com/rhel-lightspeed/okp-mcp"
LABEL vendor="Red Hat, Inc."
LABEL summary="Red Hat Offline Knowledge Portal"

# Copy the dependency venv from the builder stage. It keeps the SAME path it was
# created at in the builder, so console-script shebangs stay valid without
# rewriting.
COPY --from=builder /opt/.venvs/okp-mcp /opt/.venvs/okp-mcp

# License required by Red Hat preflight certification.
COPY LICENSE /licenses/LICENSE

# Put the venv on PATH so its console scripts and interpreter resolve first.
ENV PATH=/opt/.venvs/okp-mcp/bin:${PATH}
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

# Liveness probe: exec-form TCP connect to the listening port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python3", "-c", "import os,socket,sys; p=int(os.getenv('MCP_PORT','8000')); s=socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1', p)) == 0 else 1)"]

# Run as the image's non-root user (UID 65532).
USER 65532

# Relative path: the runtime resolves this against PATH via execvp.
ENTRYPOINT ["okp-mcp"]
