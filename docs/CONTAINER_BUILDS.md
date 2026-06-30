# Container Builds

## Containerfiles

Two Containerfiles serve different purposes:

- **`Containerfile`** — local development and CI. Uses **prebuilt manylinux wheels** (fast). `podman-compose up -d` runs it with Solr.
- **`Containerfile-source`** — Konflux CI (PR and push PipelineRuns). Compiles **every wheel from source** in a hermetic environment. Used for production builds.

Both are multi-stage builds on Red Hat UBI 10 images:

- **Builder**: `registry.access.redhat.com/ubi10/ubi` (has shell + dnf, Python 3.12 installed via dnf)
- **Runtime**: `registry.access.redhat.com/ubi10/python-312-minimal` (has shell + microdnf, runs as UID 1001)

The build runs entirely as the non-root user; there is no `USER 0` escalation.

## Build Toolchain (pip-based)

Both Containerfiles use `scripts/container-install.sh` to install dependencies. The script uses **pip** exclusively — no uv. uv ships no RPM and cannot be fetched in a hermetic Konflux build (network off, not in the offline mirror). A single pip path works in both environments:

1. **Throwaway build venv** — installs the hatchling backend (prebuilt path) or the full transitive build tree (from-source path)
2. **Build the okp_mcp wheel** from the local source tree (`pip wheel --no-build-isolation`)
3. **Application venv** — installs hash-pinned runtime deps from `.konflux/requirements.txt`
4. **Install the local wheel** into the app venv (`pip install --no-deps --no-index`)

The `BUILD_FROM_SOURCE` env var controls the binary flag:

- **Unset** → `--only-binary=:all:` (manylinux wheels, fast)
- **Set to `"1"`** → `--no-binary=:all:` (compiles every C/Rust extension, slow but hermetic-proof)

The app venv path (`/opt/app-root/src/.venvs/okp-mcp`) is identical in both stages so console-script shebangs stay valid.

## Hermetic Builds (Konflux + Cachi2)

Konflux PipelineRuns set `hermetic: "true"` and a populated `prefetch-input`. Cachi2 prefetches all dependencies before the build, then the pipeline runs with the network off:

```yaml
- name: hermetic
  value: "true"
- name: prefetch-input
  value: '[{"type": "pip", "path": ".", "requirements_files": [".konflux/requirements.txt"], "requirements_build_files": [".konflux/requirements-build-all.txt", ".konflux/requirements-build-pypi.txt"]}, {"type": "rpm", "path": "."}]'
```

Three dependency manifests are prefetched:

| File | Contents |
|------|----------|
| `.konflux/requirements.txt` | Hash-pinned runtime deps |
| `.konflux/requirements-build-all.txt` | All transitive build deps (every PEP 517 backend: maturin, setuptools-rust, etc.) |
| `.konflux/requirements-build-pypi.txt` | PyPI-only build deps unavailable on the Konflux artifact proxy |
| `rpms.lock.yaml` | RPM build-toolchain (gcc, rust, cmake, etc.) |

`uv.lock` is the single source of truth. All `.konflux/*.txt` files are **generated** by `scripts/konflux_requirements.sh`, never hand-edited. `make check-konflux-requirements` (run in CI) re-exports and fails if they drift. Regenerate with `make konflux-requirements` after any `uv.lock` or build-system change, then commit.

`rpms.lock.yaml` is generated from `rpms.in.yaml` by `make rpm-lock`, which runs the `rpm-lockfile-prototype` container against the builder image. The builder image digest is read from `Containerfile-source` to avoid duplication.

**Win32-only deps are pruned.** `uv export` emits Windows-only transitive deps (`pywin32` via `mcp`, `pywin32-ctypes` via `keyring`, `colorama`) with a `sys_platform == 'win32'` marker. Cachi2/hermeto ignores environment markers, so `konflux_requirements.sh` drops them via `uv export --prune colorama --prune pywin32 --prune pywin32-ctypes`. If a new win32-only transitive dep appears, add another `--prune <pkg>`.

## Local Hermetic Validation

```bash
make hermeto-prefetch   # run hermeto locally (requires podman)
make hermeto-clean      # remove .hermeto-out/
```

## Containerfile Notes

- The distroless-like runtime has a shell (python-312-minimal includes it) but `ENTRYPOINT` is still `["okp-mcp"]` (relative, resolved via `PATH`)
- `COMMIT_SHA` is passed as a build arg and set as an `ENV` in the runtime stage
- `HEALTHCHECK` uses `python -c` socket check on port 8000
- All runtime deps are distributed as manylinux wheels; the distroless image needs no extra shared libraries beyond glibc
