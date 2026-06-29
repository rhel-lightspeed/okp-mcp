#!/bin/bash
# Regenerate the Hermeto-consumable dependency manifests for hermetic Konflux builds.
#
# uv.lock stays the single source of truth. These manifests are generated
# artifacts derived from it:
#   .konflux/requirements.txt        runtime deps (hash-pinned, from uv.lock)
#   .konflux/requirements-build.txt  transitive build deps (hash-pinned)
#
# The hermetic build (Hermeto type:pip) prefetches these; the Containerfile
# installs from the offline mirror. Local/dev builds still use `uv sync`.
#
# Run this whenever uv.lock or the build-system requirement changes, then
# commit the regenerated files. CI verifies they are in sync (see Makefile).
set -euo pipefail

KONFLUX_DIR=".konflux"
REQ_FILE="${KONFLUX_DIR}/requirements.txt"
BUILD_FILE="${KONFLUX_DIR}/requirements-build.txt"
BUILD_ALL_FILE="${KONFLUX_DIR}/requirements-build-all.txt"

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"
mkdir -p "${KONFLUX_DIR}"

# Runtime deps: exported straight from uv.lock (hashes included, project itself
# excluded). --frozen fails if uv.lock is stale, preserving the lock guarantee.
#
# --prune drops win32-only transitive deps (pywin32 via mcp, pywin32-ctypes via
# keyring, colorama). uv export emits these with a `sys_platform == 'win32'`
# marker, but Hermeto prefetch enumerates every line and ignores markers,
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

# Build deps (hatchling only): just our own build backend and its transitive
# deps. This is all the prebuilt-wheel Containerfile needs — runtime deps
# install from manylinux wheels, no sdist compilation.
echo 'hatchling' | uv pip compile --generate-hashes - 2>/dev/null \
  | grep -vE '^(#|$)' > "${BUILD_FILE}"

# Build deps (full tree): the full transitive build dependency tree for every
# runtime dep plus our own build backend (hatchling). pybuild-deps reads
# requirements.txt and pyproject.toml build-system.requires, resolves every
# PEP 517 build backend needed to compile each sdist, and outputs a hash-pinned
# manifest. This is used by Containerfile-source (BUILD_FROM_SOURCE=1) where
# every wheel is compiled from source.
#
# pybuild-deps expects ~/.cache/pybuild-deps to exist; create it in case the
# parent directory is missing (e.g. CI runners).
mkdir -p "${HOME}/.cache/pybuild-deps"

uvx pybuild-deps compile \
  --generate-hashes \
  --no-header \
  -o "${BUILD_ALL_FILE}" \
  "${REQ_FILE}"

# Pin uv-build to a version compatible with UBI 10's rustc (1.92). pybuild-deps
# resolves the latest version, but uv-build >=0.11.8 requires rustc >=1.93 and
# the from-source hermetic build compiles it from sdist. 0.11.7 is the newest
# release with MSRV 1.92. py-key-value-aio accepts >=0.11.4,<0.12.
awk '
  /^uv-build==/ {
    print "uv-build==0.11.7 \\"
    print "    --hash=sha256:258e3a10929b5de79074078ba1ad8edbdd4db5d9c3cafba81f11b329eeaaca08"
    skip = 1; next
  }
  skip && /^    --hash=/ { next }
  skip { skip = 0 }
  { print }
' "${BUILD_ALL_FILE}" > "${BUILD_ALL_FILE}.tmp" && mv "${BUILD_ALL_FILE}.tmp" "${BUILD_ALL_FILE}"

# Split out packages the Konflux artifact registry proxy cannot find.
# These go into a separate file with --index-url pointing directly at PyPI.
# Add new package names to PROXY_MISSING as failures are discovered.
BUILD_PYPI_FILE="${KONFLUX_DIR}/requirements-build-pypi.txt"
PROXY_MISSING="^(setuptools-rust|vcs-versioning)=="

awk -v pattern="${PROXY_MISSING}" '
  /^[a-zA-Z]/ { is_proxy = ($0 ~ pattern) }
  { if (is_proxy) print > pypi; else print > keep }
' pypi="${BUILD_PYPI_FILE}.tmp" keep="${BUILD_ALL_FILE}.tmp" "${BUILD_ALL_FILE}"

mv "${BUILD_ALL_FILE}.tmp" "${BUILD_ALL_FILE}"
{ echo '--index-url https://pypi.org/simple/'; echo; cat "${BUILD_PYPI_FILE}.tmp"; } > "${BUILD_PYPI_FILE}"
rm -f "${BUILD_PYPI_FILE}.tmp"

echo "Wrote ${REQ_FILE} ($(grep -c '^[a-zA-Z]' "${REQ_FILE}") packages)"
echo "Wrote ${BUILD_FILE} ($(grep -c '^[a-zA-Z]' "${BUILD_FILE}") packages, hatchling only)"
echo "Wrote ${BUILD_ALL_FILE} ($(grep -c '^[a-zA-Z]' "${BUILD_ALL_FILE}") packages, full tree)"
echo "Wrote ${BUILD_PYPI_FILE} ($(grep -c '^[a-zA-Z]' "${BUILD_PYPI_FILE}") packages, direct PyPI)"
echo "Remember to commit all four files."
