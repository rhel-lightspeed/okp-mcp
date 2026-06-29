#!/bin/bash
# Regenerate the Cachi2-consumable dependency manifests for hermetic Konflux builds.
#
# pdm.lock stays the single source of truth. These manifests are generated
# artifacts derived from it:
#   .konflux/requirements.txt        runtime deps (hash-pinned, from pdm.lock)
#   .konflux/requirements-build.txt  build backend (hatchling, hash-pinned)
#
# The hermetic build (Cachi2 type:pip) prefetches these; the Containerfile
# installs from the offline mirror. Local/dev builds use `pdm install`.
#
# Run this whenever pdm.lock or the build-system requirement changes, then
# commit the regenerated files. CI verifies they are in sync (see Makefile).
set -euo pipefail

KONFLUX_DIR=".konflux"
REQ_FILE="${KONFLUX_DIR}/requirements.txt"
BUILD_FILE="${KONFLUX_DIR}/requirements-build.txt"

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"
mkdir -p "${KONFLUX_DIR}"

# Build-system requirement, parsed from pyproject.toml so it never drifts.
# hatchling is a pure-Python backend; pip-compile resolves it plus its build
# deps (packaging, pathspec, pluggy, trove-classifiers) to hash-pinned wheels.
# The hermetic build installs them wheel-only (--only-binary) into a throwaway
# tools venv and builds the okp_mcp wheel with --no-build-isolation; none of
# these reach the distroless runtime.
if [[ ! -x $(command -v tomcli) ]]; then
  echo "Unable to find tomcli. Please install it."
  exit 1
fi

BUILD_REQUIRE="$(tomcli get -F newline-list pyproject.toml build-system.requires)"
if [[ -z $BUILD_REQUIRE ]]; then
  echo "ERROR: could not extract BUILD_REQUIRE from pyproject.toml build-system.requires" >&2
  exit 1
fi

# Runtime deps: exported from pdm.lock (hashes included).
# --prod excludes dev dependencies.
#
# Filter out win32-only transitive deps (pywin32 via mcp, pywin32-ctypes via
# keyring, colorama). pdm export emits these with a `sys_platform == "win32"`
# marker, but Cachi2/hermeto prefetch enumerates every line and ignores markers,
# so it tries to fetch pywin32 for Linux, finds no distribution (Windows-only
# wheels, no sdist), and fails the build. The runtime is always Linux/distroless,
# so these packages are never installed anyway.
pdm export --prod --no-extras -o "${REQ_FILE}.tmp"

# Drop the entire stanza (version line + indented hash lines) for each filtered
# package so no orphaned --hash= lines remain in the output.
awk '
  BEGIN { skip = 0 }
  /^(colorama|pywin32|pywin32-ctypes)==/ { skip = 1; next }
  skip && /^[[:space:]]+--hash=/ { next }
  { skip = 0; print }
' "${REQ_FILE}.tmp" > "${REQ_FILE}"
rm -f "${REQ_FILE}.tmp"

# Build backend: hash-pinned so the hermetic build can build the okp_mcp wheel
# with --no-build-isolation against the prefetched mirror.
# pip-compile (from pip-tools, a dev dependency) handles build-system deps
# that pdm export doesn't cover.
echo "${BUILD_REQUIRE}" | pdm run pip-compile - \
  --generate-hashes \
  --no-annotate \
  --no-header \
  --allow-unsafe \
  -o "${BUILD_FILE}"

echo "Wrote ${REQ_FILE} ($(grep -c '^[a-zA-Z]' "${REQ_FILE}") packages)"
echo "Wrote ${BUILD_FILE}"
echo "Remember to commit both files."
