#!/bin/bash
# scripts/container-install.sh — Install Python dependencies inside a container build.
#
# Shared by Containerfile (prebuilt wheels) and Containerfile-source (from-source).
# The only behavioral difference is controlled by BUILD_FROM_SOURCE (see below).
#
# Required environment variables (set via Containerfile ENV):
#   VENVS                    — base directory for virtual environments
#   UV_PROJECT_ENVIRONMENT   — path to the application venv
#   HOME                     — home directory (wheel output lands here)
#
# Optional:
#   BUILD_FROM_SOURCE        — set to "1" to compile all wheels from source
#                              (passes --no-binary to pip/uv instead of --only-binary)
set -euo pipefail

# --- Determine pip binary-handling flags ---
# Prebuilt-wheel builds use --only-binary=:all: (fast, uses manylinux wheels).
# From-source builds use --no-binary=:all: (compiles every C/Rust extension).
if [ "${BUILD_FROM_SOURCE:-0}" = "1" ]; then
    PIP_BINARY_FLAG="--no-binary=:all:"
    UV_BINARY_FLAG="--no-binary"
    export CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)"
    export MAX_JOBS="$(nproc)"
    export CARGO_BUILD_JOBS="$(nproc)"
    export MAKEFLAGS="-j$(nproc)"
else
    PIP_BINARY_FLAG="--only-binary=:all:"
    UV_BINARY_FLAG=""
fi

if [ -f /cachi2/cachi2.env ]; then
    # --- Hermetic Konflux build (Cachi2 present) ---
    # Network is disabled. Cachi2 has prefetched every dependency into an offline
    # mirror and written PIP_FIND_LINKS + PIP_NO_INDEX into cachi2.env.

    # Source the Cachi2 environment so pip resolves packages from the offline mirror.
    # shellcheck source=/dev/null
    . /cachi2/cachi2.env

    # 1. Throwaway build venv: install the hatchling build backend so we can
    #    build the okp_mcp wheel without network access. This venv is never
    #    copied to the runtime stage.
    python3 -m venv "${VENVS}/build"
    "${VENVS}/build/bin/pip" install --no-cache-dir "${PIP_BINARY_FLAG}" --require-hashes \
        -r .konflux/requirements-build.txt

    # 2. Build the okp_mcp wheel from the local source tree.
    #    --no-build-isolation: use the hatchling we just installed (no PyPI fetch).
    #    --no-deps: the wheel has no bundled dependencies.
    "${VENVS}/build/bin/pip" wheel --no-cache-dir --no-build-isolation --no-deps . -w "${HOME}/wheels"

    # 3. Application venv: install only the hash-pinned runtime dependencies.
    python3 -m venv "${UV_PROJECT_ENVIRONMENT}"
    "${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir "${PIP_BINARY_FLAG}" --require-hashes \
        -r .konflux/requirements.txt

    # 4. Install the locally-built okp_mcp wheel into the app venv.
    #    --no-deps --no-index: no PyPI, no transitive resolution — just the wheel.
    "${UV_PROJECT_ENVIRONMENT}/bin/pip" install --no-cache-dir --no-deps --no-index \
        --find-links "${HOME}/wheels" okp_mcp

    # 5. Smoke-test: verify the package is importable before finishing the layer.
    "${UV_PROJECT_ENVIRONMENT}/bin/python" -c "import okp_mcp"
else
    # --- Local / non-hermetic build ---
    # Network available. Use uv for fast, locked dependency resolution.

    # Install a pinned version of uv into a throwaway tools venv.
    python3 -m venv "${VENVS}/tools"
    "${VENVS}/tools/bin/python" -m pip install --no-cache-dir uv==0.11.14

    # Use the explicit path so the script works regardless of PATH.
    UV="${VENVS}/tools/bin/uv"

    # Create the app venv with --seed (ships pip, matching the hermetic path's
    # python -m venv behavior).
    "${UV}" venv --seed "${UV_PROJECT_ENVIRONMENT}"

    # Sync from uv.lock. --locked fails if the lock is stale.
    # shellcheck disable=SC2086  # UV_BINARY_FLAG intentionally word-splits when non-empty
    "${UV}" sync --locked --no-cache --no-dev --no-editable ${UV_BINARY_FLAG}
fi
