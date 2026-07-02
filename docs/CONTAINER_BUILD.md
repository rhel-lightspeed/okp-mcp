# Container Build Process

Single `Containerfile`, multi-stage build on Red Hat Hardened Images (Project Hummingbird). Both stages pinned to digests.

## Images

| Stage | Image | Capabilities |
|-------|-------|-------------|
| Builder | `registry.access.redhat.com/hi/python:3.12-builder` | bash, dnf, pip, shadow-utils |
| Runtime | `registry.access.redhat.com/hi/python:3.12` | distroless, ~39 MB compressed, UID 65532 |

## Build Modes

The `BUILD_FROM_SOURCE` build arg (default `1`) selects the mode:

| Mode | Value | What it does | Used by |
|------|-------|-------------|---------|
| From-source | `1` | Installs C/Rust toolchain via `install-toolchain.sh`, compiles all wheels from sdist (`--no-binary`) | Konflux production |
| Prebuilt-wheel | `0` | Uses manylinux wheels (`--only-binary`), no toolchain install | GitHub Actions CI (`--build-arg BUILD_FROM_SOURCE=0`) |

## Data Flow

### Non-hermetic (local / GitHub Actions CI)

```text
uv.lock (single source of truth)
  │
  ├─ scripts/konflux_requirements.py
  │    ├─ uv export ──────────────────→ .konflux/requirements.txt        (runtime deps)
  │    ├─ uv pip compile ─────────────→ .konflux/requirements-build.txt  (hatchling only)
  │    └─ uvx pybuild-deps compile
  │         ├─ uv-build pin (rustc compat) ──→ .konflux/requirements-build-all.txt  (full build tree)
  │         └─ proxy-missing split ──────────→ .konflux/requirements-build-pypi.txt (direct PyPI)
  │
  ├─ Containerfile
  │    ├─ COPY .konflux/ + src/ + scripts/
  │    ├─ [if BUILD_FROM_SOURCE=1] scripts/install-toolchain.sh
  │    │    └─ dnf install gcc rust cargo ... (from image repos, network on)
  │    └─ scripts/container-install.sh
  │         ├─ venv /opt/.venvs/build  ← pip install build backends
  │         ├─ pip wheel (okp_mcp)
  │         ├─ venv /opt/.venvs/okp-mcp ← pip install runtime deps + wheel
  │         └─ smoke-test: python -c "import okp_mcp"
  │
  └─ Runtime stage
       └─ COPY --from=builder /opt/.venvs/okp-mcp → same path
```

### Hermetic (Konflux + Hermeto)

```text
.tekton/pull_request.yaml / push.yaml
  │  hermetic: "true"
  │  prefetch-input:
  │    ├─ type: pip  → requirements.txt + requirements-build-all.txt + requirements-build-pypi.txt
  │    └─ type: rpm  → rpms.lock.yaml
  │
  ▼
Hermeto prefetch-dependencies task
  │  Downloads all pip sdists + RPMs into /cachi2/output/
  │  Writes /cachi2/cachi2.env (sets PIP_FIND_LINKS, PIP_NO_INDEX)
  │  Injects file:// yum repo into /etc/yum.repos.d/
  │
  ▼
Containerfile (--network none)
  ├─ scripts/install-toolchain.sh
  │    └─ Detects Hermeto-injected repo, runs dnf --disablerepo='*' --enablerepo=<hermeto-repo>
  └─ scripts/container-install.sh
       ├─ Sources /cachi2/cachi2.env → pip resolves from offline mirror
       ├─ venv /opt/.venvs/build  ← pip install --no-binary=:all: --require-hashes
       │    └─ -r requirements-build-all.txt -r requirements-build-pypi.txt
       ├─ pip wheel --no-build-isolation --no-deps . (builds okp_mcp from source)
       ├─ venv /opt/.venvs/okp-mcp ← pip install --no-binary=:all: --require-hashes -r requirements.txt
       ├─ pip install --no-deps --no-index okp_mcp (from local wheel)
       └─ smoke-test
```

## Key Containerfile Details

- Builder runs as `USER 0` (root) to create `/opt` and install packages. Ephemeral — only the venv is copied out.
- App venv path is `/opt/.venvs/okp-mcp` in both stages. pip bakes absolute-path shebangs, so this path **must match** between builder and runtime.
- `ENTRYPOINT ["okp-mcp"]` is relative — resolved via `execvp` against `PATH` (venv `bin/` is prepended).
- `COMMIT_SHA` is a build arg, set as `ENV` in runtime. `build_info.py` reads it via `os.getenv`.
- `HEALTHCHECK` uses exec-form TCP-connect probe (`python3.12 -c` socket check on port 8000). No shell required.

## Manifest Generation

`uv.lock` is the single source of truth. The `.konflux/` manifests are **generated** artifacts:

| File | Contents | Generator |
|------|----------|-----------|
| `requirements.txt` | Hash-pinned runtime deps | `uv export` |
| `requirements-build.txt` | Hatchling + transitive deps (prebuilt-wheel path only) | `uv pip compile` |
| `requirements-build-all.txt` | Full PEP 517 build tree for every sdist (from-source path) | `uvx pybuild-deps compile` |
| `requirements-build-pypi.txt` | Packages missing from Konflux artifact proxy (direct PyPI) | Split from `requirements-build-all.txt` |

Regenerate after any `uv.lock` or `pyproject.toml` build-system change:

```bash
make konflux-requirements   # regenerates all four .konflux/ files
make check-konflux-requirements  # CI gate: fails if manifests drifted from uv.lock
```

### Win32-only dep pruning

`uv export` emits Windows-only transitive deps (`pywin32`, `pywin32-ctypes`, `colorama`) with `sys_platform == 'win32'` markers. Hermeto ignores environment markers and fails on packages with no Linux distribution. `konflux_requirements.py` prunes them via `uv export --prune`. If a new win32-only transitive dep appears, add another `--prune <pkg>` flag.

### uv-build version pin

`pybuild-deps` resolves the latest `uv-build`, but `uv-build >=0.11.8` requires rustc ≥1.93. The Hummingbird builder image ships rustc 1.92, so `konflux_requirements.py` pins `uv-build==0.11.7` (MSRV 1.92). Remove this pin when the builder image ships rustc ≥1.93.

### Proxy-missing package split

Some build-dep packages are not available on the Konflux artifact registry proxy. `konflux_requirements.py` splits them into `requirements-build-pypi.txt` with `--index-url https://pypi.org/simple/` so they resolve directly from PyPI. The current proxy-missing packages are `setuptools-rust` and `vcs-versioning`. Add new package names to the `PROXY_MISSING` regex as failures are discovered.

## RPM Toolchain Deps

From-source builds need a C/Rust toolchain not shipped in the builder image. In hermetic mode, RPMs must be prefetched.

| File | Purpose |
|------|---------|
| `rpms.in.yaml` | Declares required packages + content origin repos + target arches |
| `rpms.lock.yaml` | Resolved RPM dependency tree (generated, never hand-edited) |

The `contentOrigin.repos[].repoid` in `rpms.in.yaml` must match a valid Hummingbird Pulp repo ID. The allowed repo IDs are defined in the [RHTAP EC policy](https://github.com/release-engineering/rhtap-ec-policy) — getting these wrong causes Hermeto RPM prefetch to fail silently or resolve the wrong packages.

Regenerate after editing `rpms.in.yaml`:

```bash
make rpm-lock   # resolves RPM tree against builder image, writes rpms.lock.yaml
```

`install-toolchain.sh` installs these RPMs. In hermetic mode it detects the Hermeto-injected `file://` repo and restricts `dnf` to it; in non-hermetic mode it resolves from the image's network repos.

## Build Script Reference

| Script | Purpose |
|--------|---------|
| `scripts/container-install.sh` | Shared install logic: build venv → wheel → app venv → smoke test. Branches on `BUILD_FROM_SOURCE` and `/cachi2/cachi2.env`. |
| `scripts/install-toolchain.sh` | Installs gcc, rust, cargo, openssl-devel, etc. Skipped when `BUILD_FROM_SOURCE=0`. Handles hermetic vs network RPM repos. |
| `scripts/konflux_requirements.py` | Regenerates all `.konflux/requirements*.txt` from `uv.lock`. Handles win32 pruning, uv-build pin, proxy-missing split. |
| `scripts/test-container-startup.sh` | CI smoke test: starts container, waits for healthcheck, stops. |

## Local Reproduction

### Build the container

```bash
# Prebuilt-wheel (fast, for development)
podman build -t okp-mcp --build-arg BUILD_FROM_SOURCE=0 .

# From-source (matches Konflux production)
podman build -t okp-mcp .
```

### Run with Solr

```bash
podman-compose up -d   # starts okp-mcp + Solr (rhokp-rhel9)
```

### Reproduce hermetic prefetch locally

```bash
make hermeto-prefetch   # runs Hermeto in podman, output in .hermeto-out/
make hermeto-clean      # removes .hermeto-out/
```

The full hermetic build (network-off container build against prefetched deps) can be reproduced with:

```bash
make hermeto-prefetch
buildah build --network=none \
  --volume .hermeto-out:/cachi2/output:z \
  --volume .hermeto-out/cachi2.env:/cachi2/cachi2.env:z \
  .
```

## Makefile Targets

| Target | Purpose |
|--------|---------|
| `make konflux-requirements` | Regenerate `.konflux/` manifests from `uv.lock` |
| `make check-konflux-requirements` | CI gate: fail if manifests drifted |
| `make rpm-lock` | Regenerate `rpms.lock.yaml` from `rpms.in.yaml` |
| `make hermeto-prefetch` | Run Hermeto prefetch locally (requires podman) |
| `make hermeto-clean` | Remove `.hermeto-out/` |
