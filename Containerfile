# Stage 1: Builder - UBI 10 base image with shell + dnf.
# Pinned to a digest for reproducibility.
FROM registry.access.redhat.com/ubi10/ubi:latest@sha256:516ef28e78e388d12e31618326da68e21dcfc40f767f0c37c3b57059c642a4f0 AS builder

# Install Python 3.12 and pip. The UBI base image has dnf but no Python.
RUN dnf install -y python3.12 python3.12-pip && dnf clean all

# Venv path uses the runtime image's HOME (/opt/app-root/src) so console-script
# shebangs (which bake an absolute interpreter path) stay valid across stages.
ENV VENVS=/opt/app-root/src/.venvs
ENV UV_PROJECT=/build
ENV UV_PROJECT_ENVIRONMENT=${VENVS}/okp-mcp
ENV UV_PYTHON=/usr/bin/python3

# Copy dependency files first for layer caching. .konflux holds the
# hash-pinned manifests Cachi2 prefetches for hermetic builds; they are
# generated from uv.lock by scripts/konflux_requirements.sh.
COPY pyproject.toml uv.lock README.md ${UV_PROJECT}/
COPY .konflux/ ${UV_PROJECT}/.konflux/
COPY src/ ${UV_PROJECT}/src/
COPY --chmod=0755 scripts/container-install.sh ${UV_PROJECT}/scripts/

WORKDIR ${UV_PROJECT}

# Install dependencies via the shared build script.
# See scripts/container-install.sh for detailed comments on each step.
# BUILD_FROM_SOURCE is unset here → uses prebuilt manylinux wheels (fast).
RUN scripts/container-install.sh

# Stage 2: Runtime - UBI 10 Python 3.12 Minimal (has shell, microdnf, python3.12).
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest@sha256:c060604f820e6aed184f2b61aeed8faddb5c60344b2cf6e4c6e4e478196d729e AS runtime

LABEL com.redhat.application=rhel-knowledge-bridge
LABEL com.redhat.component=rhel-knowledge-bridge
LABEL description="MCP server for the RHEL Offline Knowledge Portal"
LABEL distribution-scope=private
LABEL io.k8s.description="MCP server for the RHEL Offline Knowledge Portal"
LABEL io.k8s.display-name="RHEL Offline Knowledge Portal MCP server"
LABEL io.openshift.tags="rhel,knowledge-portal,mcp"
LABEL name="rhel-knowledge-bridge/rhel-knowledge-bridge-rhel10"
LABEL cpe="cpe:/a:redhat:rhel_knowledge_bridge:1.0::el10"
LABEL release="1.0"
LABEL version=1.0
LABEL url="https://github.com/rhel-lightspeed/okp-mcp"
LABEL vendor="Red Hat, Inc."
LABEL summary="MCP server for the RHEL Offline Knowledge Portal"

# Copy the dependency venv from the builder stage. It keeps the SAME path it was
# created at in the builder, so console-script shebangs stay valid without
# rewriting.
COPY --from=builder /opt/app-root/src/.venvs/okp-mcp /opt/app-root/src/.venvs/okp-mcp

# License required by Red Hat preflight certification.
COPY LICENSE /licenses/LICENSE

# Put the venv on PATH so its console scripts and interpreter resolve first.
ENV PATH=/opt/app-root/src/.venvs/okp-mcp/bin:${PATH}
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
  CMD ["python", "-c", "import os,socket,sys; p=int(os.getenv('MCP_PORT','8000')); s=socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1', p)) == 0 else 1)"]

# Run as the image's non-root user (UID 1001).
USER 1001

# Relative path: the runtime resolves this against PATH via execvp.
ENTRYPOINT ["okp-mcp"]
