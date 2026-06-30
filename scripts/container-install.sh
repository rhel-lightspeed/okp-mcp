#!/bin/bash
# scripts/container-install.sh — Install Python dependencies inside a container build.
#
# Shared by Containerfile (prebuilt wheels) and Containerfile-source (from-source).
# The only behavioral difference is controlled by BUILD_FROM_SOURCE (see below).
#
# pip is used throughout — no uv. uv ships no RPM and cannot be fetched in a
# hermetic Konflux build (network off, not in the offline mirror), so relying on
# it forces a fragile "hermetic vs local" branch. A single pip path works in both
# environments: in hermetic builds the prefetch task sets PIP_FIND_LINKS /
# PIP_NO_INDEX and provides an offline mirror; local builds resolve from PyPI.
# Either way the deps come from the hash-pinned .konflux manifests, which are
# generated from uv.lock by scripts/konflux_requirements.py.
#
# Required environment variables (set via Containerfile ENV):
#   VENVS                    — base directory for virtual environments
#   UV_PROJECT_ENVIRONMENT   — path to the application venv
#   HOME                     — home directory (wheel output lands here)
#
# Optional:
#   BUILD_FROM_SOURCE        — set to "1" to compile all wheels from source
#                              (passes --no-binary to pip instead of --only-binary)
set -euo pipefail

# --- Determine pip binary-handling flags ---
# Prebuilt-wheel builds use --only-binary=:all: (fast, uses manylinux wheels).
# From-source builds use --no-binary=:all: (compiles every C/Rust extension).
if [ "${BUILD_FROM_SOURCE:-0}" = "1" ]; then
    PIP_BINARY_FLAG="--no-binary=:all:"
    export CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)"
    export MAX_JOBS="$(nproc)"
    export CARGO_BUILD_JOBS="$(nproc)"
    export MAKEFLAGS="-j$(nproc)"
else
    PIP_BINARY_FLAG="--only-binary=:all:"
fi

# Hermetic Konflux builds set PIP_FIND_LINKS / PIP_NO_INDEX in the build
# environment so pip resolves from the prefetched offline mirror. Some prefetch
# layouts also ship cachi2.env as a file; source it when present (harmless
# otherwise). Local builds have neither and resolve from PyPI.
if [ -f /cachi2/cachi2.env ]; then
    # shellcheck source=/dev/null
    . /cachi2/cachi2.env
fi

# 1. Throwaway build venv: install the hatchling build backend so the okp_mcp
#    wheel can be built without network access. This venv is never copied to
#    the runtime stage.
#
#    From-source builds (BUILD_FROM_SOURCE=1) need the full transitive build
#    dependency tree (every PEP 517 backend for every sdist: maturin,
#    setuptools-rust, uv-build, etc.). Prebuilt-wheel builds only need
#    hatchling — runtime deps install from manylinux wheels, not sdists.
"${UV_PYTHON:-python3}" -m venv "${VENVS}/build"
if [ "${BUILD_FROM_SOURCE:-0}" = "1" ]; then
    # Full build tree: every PEP 517 backend needed to compile every sdist
    # (maturin, setuptools-rust, etc.). Split across three files because some
    # packages are not available on the Konflux artifact proxy.
    "${VENVS}/build/bin/pip" install --no-cache-dir "${PIP_BINARY_FLAG}" --require-hashes \
        -r .konflux/requirements-build-all.txt \
        -r .konflux/requirements-build-pypi.txt
else
    # Prebuilt-wheel path: only hatchling (our build backend) is needed.
    # Runtime deps install from manylinux wheels, no sdist compilation.
    "${VENVS}/build/bin/pip" install --no-cache-dir "${PIP_BINARY_FLAG}" --require-hashes \
        -r .konflux/requirements-build.txt
fi

# 2. Build the okp_mcp wheel from the local source tree.
#    --no-build-isolation: use the hatchling we just installed (no extra fetch).
#    --no-deps: the wheel has no bundled dependencies.
"${VENVS}/build/bin/pip" wheel --no-cache-dir --no-build-isolation --no-deps . -w "${HOME}/wheels"

# 3. Application venv: install only the hash-pinned runtime dependencies.
"${UV_PYTHON:-python3}" -m venv "${UV_PROJECT_ENVIRONMENT}"
"${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir "${PIP_BINARY_FLAG}" --require-hashes \
    -r .konflux/requirements.txt

# 4. Install the locally-built okp_mcp wheel into the app venv.
#    --no-deps --no-index: no PyPI, no transitive resolution — just the wheel.
"${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir --no-deps --no-index \
    --find-links "${HOME}/wheels" okp_mcp

# 5. Smoke-test: verify the package is importable before finishing the layer.
"${UV_PROJECT_ENVIRONMENT}/bin/python" -c "import okp_mcp"
