#!/bin/bash
# Regenerate the Cachi2-consumable dependency manifests for hermetic Konflux builds.
#
# uv.lock stays the single source of truth. These manifests are generated
# artifacts derived from it:
#   .konflux/requirements.txt        runtime deps (hash-pinned, from uv.lock)
#   .konflux/requirements-build.txt  build backend (uv_build, hash-pinned)
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

BUILDER_IMAGE="$(awk '$1 == "FROM" && $NF == "builder" { print $2; exit }' Containerfile)"
PYTHON_VERSION="$(printf '%s\n' "${BUILDER_IMAGE}" | grep -oP 'python:\K[0-9]+\.[0-9]+')"
if [ -z "${PYTHON_VERSION}" ]; then
  echo "ERROR: could not extract Python version from Containerfile builder image" >&2
  exit 1
fi

# Build-system requirement, parsed from pyproject.toml so it never drifts.
# The platform uv_build wheel bundles its backend binary, so the hermetic build
# installs it wheel-only (--only-binary) and needs nothing else to build the
# okp_mcp wheel.
BUILD_REQUIRE="$(python3 -c '
import sys, tomllib
with open("pyproject.toml", "rb") as fh:
    requires = tomllib.load(fh).get("build-system", {}).get("requires", [])
if not requires:
    sys.exit(1)
print("\n".join(requires))
')"
if [ -z "${BUILD_REQUIRE}" ]; then
  echo "ERROR: could not extract BUILD_REQUIRE from pyproject.toml build-system.requires" >&2
  exit 1
fi

# Runtime deps: exported straight from uv.lock (hashes included, project itself
# excluded). --frozen fails if uv.lock is stale, preserving the lock guarantee.
uv export \
  --frozen \
  --no-emit-project \
  --no-dev \
  --format requirements-txt \
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
