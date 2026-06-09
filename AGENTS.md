# AGENTS.md - okp-mcp

MCP server bridging LLM tool calls to a Solr-indexed Red Hat knowledge base (docs, CVEs, errata, solutions). Built on FastMCP + httpx + pydantic-settings + sentry-sdk.

## Maintenance Rule

After any code change, verify that this file is still accurate. Update it in the same PR if anything has drifted: new modules, changed function signatures, removed features, renamed files, new dependencies, etc.

## Build & Run

```bash
uv sync                          # install all deps (including dev)
uv run okp-mcp                   # run server (streamable-http, default)
uv run okp-mcp --transport stdio                        # stdio mode
uv run okp-mcp --transport streamable-http --port 8000  # explicit HTTP mode
```

## CI Commands (Makefile)

```bash
make ci          # full suite: lint + typecheck + radon + drift check + test
make setup       # install deps + pre-commit hooks
make lint        # ruff check src/ tests/
make format      # ruff format src/ tests/
make typecheck   # ty check src/
make radon       # cyclomatic complexity gate (A/B only, C+ fails)
make test        # pytest with coverage
make konflux-requirements        # regenerate .konflux hermetic manifests from uv.lock
make check-konflux-requirements  # fail if .konflux manifests drifted from uv.lock
```

## Pre-commit Hooks

Install with `pre-commit install` (or `make setup`). Hooks run automatically on `git commit`:

- **ruff** (lint + format): Auto-fixes lint issues and enforces formatting
- **gitleaks**: Blocks commits containing secrets or credentials
- **trailing-whitespace**: Strips trailing spaces (preserves markdown line breaks)
- **end-of-file-fixer**: Ensures files end with a newline
- **check-toml / check-yaml**: Validates config file syntax
- **check-merge-conflict**: Catches unresolved merge conflict markers

## Running Tests

```bash
uv run pytest                              # all tests
uv run pytest tests/test_solr.py           # single file
uv run pytest tests/test_solr.py::test_solr_query_uses_provided_shared_client  # single test
uv run pytest -k "timeout"                 # by keyword
uv run pytest -x                           # stop on first failure
uv run pytest -v --cov=okp_mcp --cov-report=term-missing  # with coverage (same as `make test`)
```

pytest is configured with `asyncio_mode = "auto"` so async tests run without explicit event loop setup. Tests are randomized via pytest-randomly.

### Functional Tests

Functional tests verify document retrieval quality by calling `_run_portal_search()` directly against a live Solr instance. No LLM is involved; assertions target the structured `PortalChunk` objects (document identity, rank position, chunk text content). This makes tests fully deterministic: same Solr index produces identical results every run.

Test scenarios live in `tests/functional_cases.py` as `FunctionalCase` dataclasses parametrized with `pytest.param`. Each case captures a known-incorrect CLA answer from a RSPEED Jira ticket: the question, expected documents, and expected chunk content.

Functional tests are **deselected by default** via `pytest_collection_modifyitems` in `tests/conftest.py`. They only run when explicitly requested with `-m functional`. They require a running OKP Solr container (`podman-compose up -d`); tests skip automatically if Solr is unreachable.

**Key files**:
- `tests/functional_cases.py`: `FunctionalCase` dataclass + parametrized RSPEED test data
- `tests/test_functional.py`: test runner calling `_run_portal_search()` with structured assertions

## Project Layout

```text
src/okp_mcp/
  __init__.py    # entry point, main(), logging config, re-exports mcp
  build_info.py  # Build-time metadata: git commit SHA + package version
  config.py      # ServerConfig (pydantic BaseSettings, MCP_* env vars)
  telemetry.py   # Optional GlitchTip/Sentry exception reporting setup
  server.py      # FastMCP instance (single `mcp` object), AppContext, lifespan
  request_id.py  # Request ID context vars, FastMCP middleware, Starlette header middleware, logging filter
  metrics.py     # Prometheus metrics: counters, histograms, /metrics endpoint, ASGI middleware
  intent.py      # Intent detection: IntentRule dataclass, INTENT_RULES registry, boost application
  portal.py      # Unified portal search: query builders, chunk conversion, RRF, single/multi-query orchestrators, formatting
  tools/
    __init__.py  # package export surface, triggers tool module imports for registration
    search.py    # search_portal MCP tool
    document.py  # get_document MCP tool + document helper functions
    run_code.py  # placeholder run_code MCP tool
    shared.py    # shared tool constants
  solr.py        # Solr query builder, BM25 paragraph extraction, RHV filtering
  bm25.py        # Pure-Python BM25Plus scorer (drop-in for rank_bm25, no numpy)
  content.py     # Boilerplate stripping, content truncation, text cleaning
  formatting.py  # Result annotation, deprecation/replacement detection, sort keys
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # functional test runner: calls _run_portal_search() against live Solr, asserts on PortalChunk results
  test_portal.py       # portal.py unit tests: query builders, chunk conversion, RRF, formatting, single/multi-query orchestrators
  test_*.py            # unit test modules mirror src structure
.pre-commit-config.yaml  # pre-commit hook definitions (ruff, gitleaks, whitespace, YAML/TOML checks)
.konflux/
  requirements.txt        # hash-pinned runtime deps, generated from uv.lock (Cachi2 prefetch)
  requirements-build.txt  # hash-pinned build backend (uv_build), generated from pyproject build-system
scripts/
  konflux_requirements.sh # regenerates the .konflux manifests from uv.lock / pyproject.toml
.github/
  CODEOWNERS               # PR review assignment (@rhel-lightspeed/developers)
  workflows/
    build.yml              # CI/CD: lint, typecheck, radon, pytest matrix, container build+push
    functional.yml         # Functional tests against live Solr (triggered after build.yml)
    scorecard.yml          # OpenSSF Scorecard: security posture, weekly + push-to-main
docs/
  SOLR_EXPLORATION.md     # Historical: original redhat-okp container schema map
openshift/
  okp-mcp.yml                   # OpenShift deployment template (Deployment, Service, ServiceAccount)
  qe-gating-stage-trigger.yml   # OpenShift Job template that triggers the auto-qe-gating GitLab pipeline after staging deploys
quadlet/
  okp.network          # shared podman network for container DNS resolution
  okp-solr-data.volume # persistent Solr index volume
  okp-solr.container   # OKP Solr search engine (rootless quadlet)
  okp-mcp.container    # OKP MCP server (rootless quadlet, depends on Solr)
  README.md            # quadlet install, usage, management, troubleshooting
SECURITY.md            # Vulnerability reporting via GitHub Security Advisories
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a new MCP tool | `src/okp_mcp/tools/` | Add `@mcp.tool` async function in the relevant module and re-export it from `tools/__init__.py` |
| Change request ID propagation or response headers | `src/okp_mcp/request_id.py`, `src/okp_mcp/__init__.py`, `src/okp_mcp/server.py` | `RequestIDContextMiddleware` mirrors FastMCP request IDs into logs, `RequestIDHeaderMiddleware` adds `X-Request-ID` to HTTP/SSE responses |
| Add/modify Prometheus metrics | `src/okp_mcp/metrics.py` | Counters, histograms, `PrometheusMiddleware` ASGI class, `/metrics` custom route |
| Add/modify intent detection | `src/okp_mcp/intent.py` | Append `IntentRule` to `INTENT_RULES` at the correct priority position |
| Change portal search logic | `src/okp_mcp/portal.py` | Query builders, chunk conversion, RRF fusion, single/multi-query orchestrators, formatting |
| Change Solr query logic | `src/okp_mcp/solr.py` | `_solr_query()` builds edismax params; `_clean_query()` for tokenization |
| Modify result formatting | `src/okp_mcp/formatting.py` | `_annotate_result()` for deprecation/EOL (used by portal.py) |
| Change content cleaning | `src/okp_mcp/content.py` | `strip_boilerplate()` regex, `truncate_content()` |
| Modify config/CLI args | `src/okp_mcp/config.py` | Add field to `ServerConfig`; auto-generates CLI arg via `MCP_` prefix |
| Add functional test case | `tests/functional_cases.py` | Add `FunctionalCase` to `FUNCTIONAL_TEST_CASES` list |
| Mock Solr responses | `tests/conftest.py` | `solr_mock` fixture uses respx |
| Deploy to OpenShift | `openshift/okp-mcp.yml` | Template with params: IMAGE, IMAGE_TAG, REPLICAS, etc. |
| Trigger QE pipeline after staging deploy | `openshift/qe-gating-stage-trigger.yml` | OpenShift Job template; calls the GitLab CI trigger API for the auto-qe-gating project. Secret `auto-qe-trigger` supplies `gitlab-url`, `project-id`, `trigger-token`. |
| Run locally with systemd | `quadlet/` | Rootless quadlet files: `.container`, `.network`, `.volume`; see `quadlet/README.md` |
| Modify pre-commit hooks | `.pre-commit-config.yaml` | Runs on every commit: ruff, gitleaks, whitespace, YAML/TOML checks |
| Change hermetic build deps | `scripts/konflux_requirements.sh`, `.konflux/` | Regenerate with `make konflux-requirements` after a `uv.lock`/build-system change; CI gates drift |
| Toggle hermetic build | `.tekton/pull_request.yaml`, `.tekton/push.yaml` | `hermetic` + `prefetch-input` params; pipeline already wires `prefetch-dependencies` |
| Modify CI/CD workflows | `.github/workflows/` | `build.yml` (test+container), `functional.yml` (Solr integration), `scorecard.yml` (OpenSSF) |
| Solr schema reference | `docs/SOLR_EXPLORATION.md` | Historical: original redhat-okp container schema map |

## Tekton Pipeline Maintenance

### Pipeline Files

- `.tekton/pipeline-build-multiarch.yaml`: Konflux multi-arch build Pipeline with task references pinned to `quay.io/konflux-ci/tekton-catalog/<task>:<version>@sha256:<digest>`.
- `.tekton/pull_request.yaml`: PipelineRun triggered on PR events.
- `.tekton/push.yaml`: PipelineRun triggered on push to main/release branches.
- `.tekton/task-get-version.yaml`: Local Task (not from catalog, no version tracking needed).

Renovate tracks Tekton task updates automatically via [org-level inherited config](https://github.com/rhel-lightspeed/renovate-config) (weekends schedule, no automerge).

### Auditing Task Versions

To check whether pinned tasks are current:

1. Extract task references: `grep 'quay.io/konflux-ci/tekton-catalog/' .tekton/pipeline-build-multiarch.yaml`
2. For each task, list available version tags:
   ```bash
   skopeo list-tags docker://quay.io/konflux-ci/tekton-catalog/<task> \
     | jq -r '.Tags[]' | grep -E '^[0-9]+\.[0-9]+(\.[0-9]+)?$' | sort -V | tail -5
   ```
3. Get the latest digest for the current (or newer) version tag:
   ```bash
   skopeo inspect docker://quay.io/konflux-ci/tekton-catalog/<task>:<version> | jq -r '.Digest'
   ```
4. Compare the canonical upstream pipeline to detect missing/added tasks or structural changes:
   ```bash
   curl -sL https://raw.githubusercontent.com/konflux-ci/build-definitions/main/pipelines/docker-build-multi-platform-oci-ta/docker-build-multi-platform-oci-ta.yaml
   ```

**zsh gotcha**: The bash tool runs in zsh. Bash-only syntax like `declare -A` associative arrays will fail. Write the script to a temp file and run it with `bash /tmp/script.sh` instead.

### Known Gaps (as of 2026-05-20)

**Missing task**: `source-build-oci-ta:0.3` - builds a source container image. Present in the canonical pipeline but absent from ours. Runs after `build-image-index`, gated by a `build-source-image` param. Needs `BINARY_IMAGE`, `BINARY_IMAGE_DIGEST`, `SOURCE_ARTIFACT`, `CACHI2_ARTIFACT` params.

**Matrix strategy migrations**: The canonical pipeline uses `matrix.params` for per-platform execution on these tasks, but our pipeline does not:
- `clair-scan` (matrix on `image-platform`)
- `clamav-scan` (matrix on `image-arch`)
- `ecosystem-cert-preflight-checks` (matrix on `platform`)

Adopting matrix strategies requires adding `matrix.params` blocks and adjusting the task param wiring. This is a structural change, not just a version bump.

**Patch version divergence**: Our `clair-scan` (0.3.2) and `clamav-scan` (0.3.1) use patch versions ahead of the canonical pipeline's `0.3`. These patch versions exist in the catalog and are valid, but may drift from canonical expectations.

## Boot Sequence

```text
uv run okp-mcp [--transport ...] [--port ...]
  → pyproject.toml: okp-mcp = "okp_mcp:main"
  → __init__.py: main()
       ├─ CliApp.run(ServerConfig)     # parse CLI + MCP_* env vars
       ├─ _configure_logging()
       ├─ telemetry.initialize_error_reporting()  # no-op unless MCP_GLITCHTIP_DSN is set
       ├─ log version + commit SHA     # build_info.py: COMMIT_SHA env var, then APP_ROOT/COMMIT_SHA file, then local `git rev-parse`
       └─ mcp.run(transport=...)       # start FastMCP server
            → server.py: _app_lifespan()
                ├─ creates shared httpx.AsyncClient
                └─ yields AppContext(...)
            → metrics.py: registers /metrics custom_route + PrometheusMiddleware
            → tools/__init__.py: imports tool modules for @mcp.tool registration
```

## Module Dependencies

```text
__init__.py → build_info, config, metrics (side-effect import), request_id, server, telemetry, tools (side-effect import)
build_info.py → (standalone; reads COMMIT_SHA env var, APP_ROOT/COMMIT_SHA file, or local `git rev-parse`)
tools/__init__.py → tools/search.py, tools/document.py, tools/run_code.py
tools/search.py → config, metrics, portal, server
tools/document.py → content, metrics, server, solr, tools/shared.py
tools/run_code.py → config, server
metrics.py  → server (imports mcp for custom_route)
request_id.py → fastmcp.server.dependencies, fastmcp.server.middleware, starlette
intent.py   → config
portal.py   → config, content, formatting, intent, solr
formatting.py → (standalone)
solr.py     → bm25, config, metrics
bm25.py     → (standalone)
server.py   → config
telemetry.py → build_info, config, sentry_sdk
content.py  → (standalone)
```

No circular imports. `content.py`, `bm25.py`, and `formatting.py` have zero internal dependencies.

## Code Style

### Python Version & Formatting
- **Target**: Python 3.12+ (CI tests 3.12, 3.13, 3.14)
- **Line length**: 120 characters
- **Formatter**: ruff format
- **Linter**: ruff check with rules: E, F, W, I (isort), UP, S (security), B (bugbear), A, C4, SIM, TID252 (ban relative imports)

### Imports
- Order: stdlib, third-party, first-party (enforced by ruff `I` rule)
- **ZERO relative imports.** Always use absolute imports with the full package name (`from okp_mcp.config import ServerConfig`, not `from .config import ServerConfig`). This is enforced by ruff rule `TID252` and will fail CI.
- Side-effect imports get a `noqa` comment explaining why:
  ```python
  from okp_mcp import tools as _tools  # noqa: F401 -- triggers @mcp.tool registration
  ```

### Type Hints
- Type checker: `ty` (not mypy/pyright)
- Use `typing.Literal` for constrained string values
- Use pydantic `Field()` with descriptions for config
- Use `@computed_field` for derived config properties
- Add `# type: ignore[prop-decorator]` on computed_field + @property combos (known ty quirk)

### Naming
- `snake_case` for functions, variables, modules
- `PascalCase` for classes
- Prefix unused imports with `_` (e.g., `_tools`)
- Constants in `UPPER_SNAKE_CASE`

### Docstrings
- PEP 257 style on every module, class, and function (including tests and fixtures)
- Module docstrings are single-line: `"""Description of the module."""`
- Test docstrings describe the behavior being verified, not the test name
- Use `noqa` comments with rule codes and explanations when suppressing lint

### Error Handling
- Return user-friendly strings on failure (not exceptions) for MCP tools
- Use specific exception types in except clauses (`httpx.TimeoutException`, not bare `Exception`)
- Log exceptions with `logger.exception()` for stack traces
- Log warnings with `logger.warning()` for expected failures (timeouts)
- **Never swallow exception details**: every `except` block that logs MUST include `exc_info=True` (for `warning`) or use `logger.exception()` (which adds it automatically). Bare `logger.warning("something failed")` without the traceback makes debugging impossible.
- Pattern:
  ```python
  try:
      ...
  except httpx.TimeoutException:
      logger.warning("...", exc_info=True)
      return "user-friendly message"
  except (httpx.HTTPError, ValueError):
      logger.exception("...")
      return "user-friendly message"
  ```

### Async
- All MCP tool functions are `async`
- Use `httpx.AsyncClient` as async context manager for HTTP calls
- pytest asyncio_mode is `auto`, so no `@pytest.mark.asyncio` needed (but existing tests may have it)

### Security Suppressions
- `# noqa: S104` on intentional `0.0.0.0` binds with comment
- `# noqa: S101` suppressed globally in tests/ (assert usage)
- Always add the rationale after the noqa comment

## Configuration Pattern

Config uses `pydantic_settings.BaseSettings` with `MCP_` env prefix. CLI via `CliApp.run()`. Precedence: CLI > env vars > defaults. Derived values use `@computed_field`.

Optional GlitchTip/Sentry exception reporting is configured with `MCP_GLITCHTIP_DSN` / `--glitchtip_dsn`. Missing or empty DSNs are handled as a no-op for local development.

Module-level constant `STOP_WORDS` lives in `config.py` outside the class to avoid circular import issues. The Solr endpoint is no longer a module-level constant — it flows through `ServerConfig.solr_endpoint` → `AppContext.solr_endpoint` at runtime.

## Testing Patterns

- **HTTP mocking**: `respx` library (not `responses` or `aioresponses`)
- **Fixtures**: shared in `conftest.py`, test-local when specific
- **Parametrize**: use `@pytest.mark.parametrize` for value variations
- **Mocking**: `unittest.mock.patch` / `patch.dict` for env vars
- **Fixture naming**: prefix unused fixtures with `_` (e.g., `_mock_mcp_run`)
- **Assert style**: direct assertions, `pytest.raises` for expected errors

## Container

- Use `Containerfile` (not Dockerfile), build with `podman`
- Multi-stage build on Red Hat Hardened Images (Project Hummingbird), both stages pinned to digests:
  - Builder: `registry.access.redhat.com/hi/python:3.12-builder` (has shell + dnf). The install step branches on whether Cachi2 prefetched dependencies (see Hermetic Builds below).
  - Runtime: `registry.access.redhat.com/hi/python:3.12` (distroless: no shell, no package manager, runs as UID 65532)
- The build runs entirely as the non-root user (UID 65532); there is no `USER 0` escalation. Both images set `HOME` to a user-owned directory, so the tools venv and the app venv are written under `${HOME}/.venvs`.
- The app venv keeps the same path (`${HOME}/.venvs/okp-mcp`) in both stages. uv/pip console scripts bake an absolute-path shebang, so relocating the venv breaks the entrypoint with "No such file or directory". Keep the builder and runtime venv paths identical.
- The distroless runtime has no shell, so `RUN` is only used in the builder stage. `ENTRYPOINT ["okp-mcp"]` is relative: the runtime resolves it via `execvp` against `PATH` (the venv `bin/` is prepended). `COMMIT_SHA` is passed as a build arg and set as an `ENV` in the runtime stage; `build_info.COMMIT_SHA` reads it via `os.getenv` at import time (no file written or copied).
- `HEALTHCHECK` uses an exec-form TCP-connect probe (`python -c` socket check on port 8000); no shell required.
- All runtime dependencies are distributed as manylinux wheels (some carry self-contained native extensions, e.g. `pydantic-core`, `cryptography`); the distroless image needs no extra shared libraries beyond glibc. A new dependency that only ships an sdist (no wheel) would break the hermetic build, which is wheel-only.
- `podman-compose up -d` to run with Solr (`rhokp-rhel9` from `registry.redhat.io`)

### Hermetic Builds (Konflux + Cachi2)

The Containerfile install step has two paths, both targeting the same venv at `${HOME}/.venvs/okp-mcp`:

- **Hermetic** (Konflux): `/cachi2/cachi2.env` exists, network is off. A stdlib `python -m venv` plus `pip install --only-binary=:all: --require-hashes` from the Cachi2 offline mirror installs the pinned deps, then `pip install --no-build-isolation .` builds the okp_mcp wheel using the prefetched `uv_build` backend (its `uv-build` binary must be on `PATH` during the build). `uv_build` is uninstalled afterwards so it never reaches the distroless runtime. No `uv` here: it cannot be fetched with the network off.
- **Local / non-hermetic**: installs pinned `uv`, then `uv sync --locked` straight from `uv.lock`.

`uv.lock` is the single source of truth. `.konflux/requirements.txt` (runtime) and `.konflux/requirements-build.txt` (build backend) are **generated** from it by `scripts/konflux_requirements.sh`, never hand-edited. The script derives its target Python version from the `Containerfile` builder image, so Renovate image tag updates do not require a separate script edit. `make check-konflux-requirements` (run in CI and `make ci`) re-exports and fails if they drift. Regenerate with `make konflux-requirements` after any `uv.lock` or build-system change, then commit.

The PipelineRuns (`.tekton/pull_request.yaml`, `.tekton/push.yaml`) set `hermetic: "true"` and a `prefetch-input` with `allow_binary` (wheel-mode prefetch; avoids sdist build-time toolchains and the `uv` sdist Cargo.lock issue). The shared `pipeline-build-multiarch.yaml` already wires the `prefetch-dependencies` task and `CACHI2_ARTIFACT` into `build-images`; no pipeline change is needed to toggle hermetic mode.

To reproduce a hermetic build locally: `hermeto fetch-deps` (via `quay.io/konflux-ci/hermeto`) with the PipelineRun's `prefetch-input`, `generate-env`/`inject-files` into `/cachi2`, then `buildah build --network=none --volume <out>:/cachi2/output --volume <out>/cachi2.env:/cachi2/cachi2.env`.

## Complexity

All functions must be rated A or B by radon. C or higher fails the CI gate. Refactor until compliant.

## Pre-PR Code Review

Before creating a pull request, check if `coderabbit` is available in `$PATH`. If it is, ask the user whether they'd like a CodeRabbit review before opening the PR. Run it with structured output for easy parsing:

```bash
coderabbit review --agent --base <base-branch> -c .coderabbit.yaml
```

The CLI does not auto-read `.coderabbit.yaml` from the repo root. Always pass `-c .coderabbit.yaml` so local reviews match the GitHub PR review behavior (tone, path instructions, review profile).

If findings come back, address them before creating the PR (or flag them for the user). Zero findings means good to go.

## Workarounds

- `run_code()` in `src/okp_mcp/tools/run_code.py` is a KLUDGE: placeholder tool that prevents Gemini 2.5 Flash from crashing when it tries to use its built-in code execution tool. Returns a polite "not supported" message. Do not remove without verifying Gemini behavior first.
