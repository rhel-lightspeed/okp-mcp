#!/bin/bash
# Regenerate the Cachi2-consumable dependency manifests for hermetic Konflux builds.
#
# uv.lock stays the single source of truth. These manifests are generated
# artifacts derived from it:
#   .konflux/requirements.txt        runtime deps (hash-pinned, from uv.lock)
#   .konflux/requirements-build.txt  build backend (hatchling, hash-pinned)
#
# The hermetic build (Cachi2 type:pip) prefetches these; the Containerfile
# installs from the offline mirror. Local/dev builds still use `uv sync`.
#
# Run this whenever uv.lock or the build-system requirement changes, then
# commit the regenerated files. CI verifies they are in sync (see Makefile).
set -euo pipefail

KONFLUX_DIR=".konflux"
REQ_FILE="${KONFLUX_DIR}/requirements.txt"
BUILD_FILE="${KONFLUX_DIR}/requirements-build.txt"

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"
mkdir -p "${KONFLUX_DIR}"

PYTHON_VERSION="$(grep -E '^FROM\s+.*python:[0-9]+\.[0-9]+' Containerfile | head -1 | grep -oE '[0-9]+\.[0-9]+')"
if [ -z "${PYTHON_VERSION}" ]; then
  echo "ERROR: could not extract Python version from Containerfile builder image" >&2
  exit 1
fi

# Build-system requirement, parsed from pyproject.toml so it never drifts.
# hatchling is a pure-Python backend; uv pip compile resolves it plus its build
# deps (packaging, pathspec, pluggy, trove-classifiers) to hash-pinned wheels.
# The hermetic build installs them wheel-only (--only-binary) into a throwaway
# tools venv and builds the okp_mcp wheel with --no-build-isolation; none of
# these reach the distroless runtime. hatchling's deps carry no win32-only
# markers, so the Cachi2 prefetch enumerates the build manifest cleanly.
if [[ ! -x $(command -v tomcli) ]]; then
  echo "Unable to find tomcli. Please install it."
  exit 1
fi

BUILD_REQUIRE="$(tomcli get -F newline-list pyproject.toml build-system.requires)"
if [[ -z $BUILD_REQUIRE ]]; then
  echo "ERROR: could not extract BUILD_REQUIRE from pyproject.toml build-system.requires" >&2
  exit 1
fi

# Runtime deps: exported straight from uv.lock (hashes included, project itself
# excluded). --frozen fails if uv.lock is stale, preserving the lock guarantee.
#
# --prune drops win32-only transitive deps (pywin32 via mcp, pywin32-ctypes via
# keyring, colorama). uv export emits these with a `sys_platform == 'win32'`
# marker, but Cachi2/hermeto prefetch enumerates every line and ignores markers,
# so it tries to fetch pywin32 for Linux, finds no distribution (Windows-only
# wheels, no sdist), and fails the build. The runtime is always Linux/distroless,
# so these packages are never installed anyway.
uv export \
  --frozen \
  --no-emit-project \
  --no-dev \
  --format requirements-txt \
  --prune colorama \
  --prune pywin32 \
  --prune pywin32-ctypes \
  -o "${REQ_FILE}"

# Build backend: hash-pinned so the hermetic build can build the okp_mcp wheel
# with --no-build-isolation against the prefetched mirror.
echo "${BUILD_REQUIRE}" | uv pip compile - \
  --generate-hashes \
  --python-version "${PYTHON_VERSION}" \
  --no-annotate \
  --no-header \
  -o "${BUILD_FILE}"

echo "Wrote ${REQ_FILE} ($(grep -c '^[a-zA-Z]' "${REQ_FILE}") packages)"
echo "Wrote ${BUILD_FILE}"
echo "Remember to commit both files."
