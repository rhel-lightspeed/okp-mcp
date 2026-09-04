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
# Stateless mode is enabled by default for streamable-http.
# Disable with: uv run okp-mcp --stateless-http false
# or: MCP_STATELESS_HTTP=false uv run okp-mcp
```

## CI Commands (Makefile)

```bash
make ci          # full suite: lint + typecheck + radon + drift check + test
make setup       # install deps + pre-commit hooks
make fix         # ruff check --fix + ruff format (auto-fix)
make lint        # ruff check src/ tests/
make format      # ruff format src/ tests/
make typecheck   # ty check src/
make radon       # cyclomatic complexity gate (A/B only, C+ fails)
make test        # pytest with coverage
make konflux-requirements        # regenerate .konflux hermetic manifests from uv.lock
make check-konflux-requirements  # fail if .konflux manifests drifted from uv.lock
make rpm-lock                    # regenerate rpms.lock.yaml from rpms.in.yaml
make lock                        # resolve deps and update uv.lock
make freeze                      # lock + regenerate .konflux manifests (preferred workflow)
make hermeto-prefetch             # run Hermeto prefetch locally (requires podman)
make hermeto-clean                # remove .hermeto-out/
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

A second functional module, `tests/test_functional_document.py`, exercises the `get_document` retrieval layer against live Solr. It seeds a real document via `_run_portal_search()`, then calls `_fetch_document_with_query()` / `_fetch_document_raw()` to prove the doc_id drives retrieval while the caller's query only selects highlights (it does not gate retrieval), and that a visible document URL round-trips through `_normalize_doc_id()` before the fetch layer.

**Key files**:
- `tests/functional_cases.py`: `FunctionalCase` dataclass + parametrized RSPEED test data
- `tests/test_functional.py`: test runner calling `_run_portal_search()` with structured assertions
- `tests/test_functional_document.py`: `get_document` retrieval tests (query-does-not-gate, highlight selection, URL normalization round-trip)

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
  content.py     # Boilerplate stripping, content truncation, text cleaning, section outline
  outline.py     # Section anchors parsed from the OKP HTML mirror (fetch + LRU cache)
  formatting.py  # Result annotation, deprecation/replacement detection, sort keys
  types.py       # Shared TypedDicts: SolrDoc, SolrHighlighting, SolrResponseBody, SolrResponse
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # functional test runner: calls _run_portal_search() against live Solr, asserts on PortalChunk results
  test_functional_document.py  # functional get_document tests: query does not gate retrieval, highlight selection, URL normalization round-trip
  test_portal.py       # portal.py unit tests: query builders, chunk conversion, RRF, formatting, single/multi-query orchestrators
  test_*.py            # unit test modules mirror src structure
.pre-commit-config.yaml  # pre-commit hook definitions (ruff, gitleaks, whitespace, YAML/TOML checks)
.konflux/
  requirements.txt            # hash-pinned runtime deps (generated from uv.lock)
  requirements-build.txt      # hatchling + transitive build deps, prebuilt-wheel path only (generated)
  requirements-build-all.txt  # full PEP 517 build tree for from-source path (generated)
  requirements-build-pypi.txt # packages missing from Konflux artifact proxy (generated)
scripts/
  konflux_requirements.py     # regenerates the .konflux manifests from uv.lock / pyproject.toml
  container-install.sh        # shared container install logic (build venv → wheel → app venv)
  install-toolchain.sh        # installs C/Rust build toolchain for from-source builds
  test-container-startup.sh   # CI smoke test: start container, wait for healthcheck
rpms.in.yaml                  # build-toolchain RPM packages for hermetic prefetch
rpms.lock.yaml                # resolved RPM dependency tree (generated from rpms.in.yaml)
.github/
  CODEOWNERS               # PR review assignment (@rhel-lightspeed/developers)
  workflows/
    ci.yml                 # CI: lint, typecheck, radon, pytest matrix, container build+push
    manifests.yml          # Hermetic manifest drift check (runs only when uv.lock or .konflux/ change)
    functional.yml         # Functional tests against live Solr (triggered after ci.yml)
    scorecard.yml          # OpenSSF Scorecard: security posture, weekly + push-to-main
docs/
  CONTAINER_BUILD.md         # Container build process, data flow diagrams, hermetic builds
  RELEASE_BRANCHES.md        # Release branch workflow
  SOLR_EXPLORATION.md        # Historical: original redhat-okp container schema map
  UPDATING_REQUIREMENTS.md   # Dependency update workflow: make freeze, lock, konflux-requirements
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

## Section Anchors (get_document outlines)

`get_document` puts real URL fragments on both documentation paths: without a query it
returns the page's section outline instead of content, and with a query it labels each
returned passage with the section it came from. Either way the caller can link straight to
a section rather than to the whole guide.

The anchors cannot come from Solr. Red Hat assigns them in the AsciiDoc source -- "Kafka
tuning overview" is published at `#con-config-tuning-intro-str` -- and no indexed field
carries them; deriving slugs from heading text matched 1 heading in 44 when measured
against a live guide.

They come from the OKP appliance instead, which serves the rendered HTML over httpd on
port 8080 alongside Solr on 8983. Solr document ids *are* the mirror's paths, so
`outline.py` fetches `{html_mirror_url}{id}` and parses the `<section id>` elements.
Measured on a 250-document sample: all reachable, 236 expose `<section id>`, and the
remaining 14 are single-topic pages with no subsections. Anchors produced for one guide
matched the public docs.redhat.com fragments 45 of 45.

- Configure with `MCP_OKP_HTML_URL`; it defaults to the `solr_url` host on port 8080.
  Set it to `-` to disable the lookup.
- In-container deployments need no configuration: the mcp container shares the pod
  network, so the derived `http://<solr-host>:8080` already resolves to the mirror.
- `podman-compose.yml` publishes the mirror as `8085:8080` for running the server from
  source on the host; `.mcp.json` sets `MCP_OKP_HTML_URL` to match. Host port 8080 is
  avoided because it is heavily contended -- pointing the mirror at an unrelated local
  service yields an outline with no anchors rather than wrong ones, but it is still a
  wasted request per lookup.
- Passages are placed by locating their text in the mirror's body via
  `DocumentOutline.locate()`. Solr's `main_content` cannot be used for this: it leads with
  the page's table of contents, which repeats most headings, so offsets computed against it
  attribute passages to whichever heading the ToC listed. Measured over 146 highlight
  passages from 33 guides: all 104 drawn from real prose were placed correctly and the 42
  misses were every one a ToC fragment -- so `locate()` returning None means "not body
  text", and those passages keep a bare `Passage N:` label rather than borrowing a
  neighbour's anchor. (That also makes an unplaceable passage a reliable ToC detector, if
  filtering them out is ever wanted.)
- Passages that `locate()` cannot place are dropped as ToC noise, but only when prose
  remains: an unavailable mirror makes everything look unplaceable, and a reference manual
  can legitimately highlight nothing but heading runs. Measured over 39 guide+query pairs,
  9 (23%) shed a ToC passage for -17% passage characters; 13 returned nothing but ToC and
  were left alone. Filtering is a subset in Solr's own order, so relevance cannot regress.
- Do NOT raise `hl.snippets` to compensate for the dropped passages. It was tried:
  `hl.snippets` re-fragments the field rather than extending the list, and going from 10 to
  24 pushed the three relevant admission passages out of the top three in favour of the
  glossary. Aggregate passage and character counts improved while the answer got worse.
- Anchoring covers Solr highlight passages. The BM25 fallback path
  (`_extract_relevant_section`) is not anchored yet.
- Outlines are trimmed to `_MAX_OUTLINE_CHARS` (15K) by dropping the deepest nesting
  levels, not by truncating the list. Mirror outlines run to a median of 22 sections but
  a p90 of 181 and a max of 460 (~37KB), and the sections a reader wants are as often in
  the last chapter as the first -- a flat count cap dropped OpenShift's Chapter 9
  entirely. The 460-section worst case degrades to its top three levels at ~11KB.
- Every failure (mirror down, document absent, page without sections) falls back to a
  title-only outline built from `heading_h1`/`heading_h2`. The lookup must never turn a
  working `get_document` call into an error.

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a new MCP tool | `src/okp_mcp/tools/` | Add `@mcp.tool` async function in the relevant module and re-export it from `tools/__init__.py` |
| Change request ID propagation or response headers | `src/okp_mcp/request_id.py`, `src/okp_mcp/__init__.py`, `src/okp_mcp/server.py` | `RequestIDContextMiddleware` mirrors FastMCP request IDs into logs, `RequestIDHeaderMiddleware` adds `X-Request-ID` to HTTP/SSE responses |
| Add/modify Prometheus metrics | `src/okp_mcp/metrics.py` | Counters, histograms, `PrometheusMiddleware` ASGI class, `/metrics` custom route |
| Add/modify intent detection | `src/okp_mcp/intent.py` | Append `IntentRule` to `INTENT_RULES` at the correct priority position |
| Change portal search logic | `src/okp_mcp/portal.py` | Query builders, chunk conversion, RRF fusion, single/multi-query orchestrators, formatting |
| Change Solr query logic | `src/okp_mcp/solr.py` | `_solr_query()` builds edismax params; `_clean_query()` for tokenization |
| Change document retrieval query | `src/okp_mcp/tools/document.py` | `_fetch_document_with_query()` selects the doc via `q` under `defType=lucene` and passes the caller's query as `hl.q` (highlights only) so `mm` never gates retrieval; `_doc_id_filter()` normalizes ID suffix forms |
| Modify result formatting | `src/okp_mcp/formatting.py` | `annotate_result()` for deprecation/EOL (used by portal.py) |
| Change content cleaning | `src/okp_mcp/content.py` | `strip_boilerplate()` regex, `truncate_content()` |
| Change the section outline | `src/okp_mcp/content.py` | `format_sections()` renders anchors from `outline.py` when present, else falls back to `heading_h1`/`heading_h2` titles. `_fit_to_budget()` trims to `_MAX_OUTLINE_CHARS` by shedding the deepest nesting levels first, never by cutting the tail; `clean_heading()` normalizes the NBSP numbering separators |
| Change section anchor lookup | `src/okp_mcp/outline.py` | `parse_document()` extracts `<section id>` sections plus locatable body text with stdlib `html.parser`; `DocumentOutline.locate()` maps a passage to its section; `OutlineFetcher` fetches and LRU-caches parses from the HTML mirror. Every failure degrades to `NO_OUTLINE` — never raise |
| Modify config/CLI args | `src/okp_mcp/config.py` | Add field to `ServerConfig`; auto-generates CLI arg and `MCP_`-prefixed env var |
| Enable stateless mode | `src/okp_mcp/config.py` | Enabled by default. `--stateless-http false` or `MCP_STATELESS_HTTP=false` to disable |
| Add functional test case | `tests/functional_cases.py` | Add `FunctionalCase` to `FUNCTIONAL_TEST_CASES` list |
| Mock Solr responses | `tests/conftest.py` | `solr_mock` fixture uses respx |
| Deploy to OpenShift | `openshift/okp-mcp.yml` | Template with params: IMAGE, IMAGE_TAG, REPLICAS, etc. |
| Trigger QE pipeline after staging deploy | `openshift/qe-gating-stage-trigger.yml` | OpenShift Job template; calls the GitLab CI trigger API for the auto-qe-gating project. Secret `auto-qe-trigger` supplies `gitlab-url`, `project-id`, `trigger-token`. |
| Run locally with systemd | `quadlet/` | Rootless quadlet files: `.container`, `.network`, `.volume`; see `quadlet/README.md` |
| Modify pre-commit hooks | `.pre-commit-config.yaml` | Runs on every commit: ruff, gitleaks, whitespace, YAML/TOML checks |
| Update Python dependencies | `pyproject.toml`, `uv.lock`, `.konflux/` | See [docs/UPDATING_REQUIREMENTS.md](docs/UPDATING_REQUIREMENTS.md); `make freeze` is the preferred single command |
| Change hermetic build deps | `scripts/konflux_requirements.py`, `.konflux/` | Regenerate with `make konflux-requirements` after a `uv.lock`/build-system change; CI gates drift |
| Change RPM toolchain deps | `rpms.in.yaml`, `scripts/install-toolchain.sh` | Edit `rpms.in.yaml`, run `make rpm-lock`, update `install-toolchain.sh` if package list changed |
| Change container install logic | `scripts/container-install.sh`, `Containerfile` | Build venv → wheel → app venv; branches on `BUILD_FROM_SOURCE` and `/cachi2/cachi2.env` |
| Toggle hermetic build | `.tekton/pull_request.yaml`, `.tekton/push.yaml` | `hermetic` + `prefetch-input` params; pipeline already wires `prefetch-dependencies` |
| Modify CI/CD workflows | `.github/workflows/` | `ci.yml` (test+container), `manifests.yml` (hermetic drift), `functional.yml` (Solr integration), `scorecard.yml` (OpenSSF) |
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

### Known Gaps (as of 2026-07-03)

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
tools/document.py → content, metrics, outline, server, solr, tools/shared.py, types
tools/run_code.py → config, server
metrics.py  → server (imports mcp for custom_route)
request_id.py → fastmcp.server.dependencies, fastmcp.server.middleware, starlette
intent.py   → config
portal.py   → config, content, formatting, intent, solr, types
formatting.py → (standalone)
solr.py     → bm25, config, metrics, types
bm25.py     → (standalone)
server.py   → config, outline
telemetry.py → build_info, config, sentry_sdk
content.py  → outline, types
outline.py  → (standalone; stdlib html.parser + httpx)
types.py    → (standalone)
```

No circular imports. `types.py`, `bm25.py`, and `formatting.py` have zero internal dependencies.

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

### Query Content Logging (CWE-532)
- **Never log user query content at INFO level or above.** Tool arguments derive from end-user prompts and may contain hostnames, internal IPs, or error messages. Log operational data only: query count, result counts, doc IDs (Red Hat paths), timing, error types.
- Solr query parameters (`q`, `fq`) are logged at **DEBUG** only, since `q` carries the user's cleaned query for search calls.
- The `doc_id` parameter (a Red Hat documentation path, not user-supplied PII) is safe to log at INFO.
- Security audit: RSPEED-3365, CWE-532, FIND-005.

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

Optional GlitchTip/Sentry exception reporting is configured with `MCP_GLITCHTIP_DSN` / `--glitchtip-dsn`. Missing or empty DSNs are handled as a no-op for local development.

Module-level constant `STOP_WORDS` lives in `config.py` outside the class to avoid circular import issues. The Solr endpoint is no longer a module-level constant — it flows through `ServerConfig.solr_endpoint` → `AppContext.solr_endpoint` at runtime.

## Testing Patterns

- **HTTP mocking**: `respx` library (not `responses` or `aioresponses`)
- **Fixtures**: shared in `conftest.py`, test-local when specific
- **Parametrize**: use `@pytest.mark.parametrize` for value variations
- **Mocking**: `unittest.mock.patch` / `patch.dict` for env vars
- **Fixture naming**: prefix unused fixtures with `_` (e.g., `_mock_mcp_run`)
- **Assert style**: direct assertions, `pytest.raises` for expected errors

## Container

Full build process documentation with data flow diagrams: **[docs/CONTAINER_BUILD.md](docs/CONTAINER_BUILD.md)**.

Quick reference:

- Single `Containerfile`, multi-stage on Hummingbird images (builder + distroless runtime), both pinned to digests
- `BUILD_FROM_SOURCE=1` (default): compiles all wheels from source (Konflux production). `BUILD_FROM_SOURCE=0`: prebuilt manylinux wheels (GitHub Actions CI)
- Build logic lives in `scripts/container-install.sh` and `scripts/install-toolchain.sh`
- Hermetic builds are **enabled** — Hermeto prefetches pip sdists + RPMs; `container-install.sh` detects `/cachi2/cachi2.env` and resolves offline
- `uv.lock` is the single source of truth; `.konflux/requirements*.txt` + `rpms.lock.yaml` are generated artifacts
- Regenerate after dep changes: `make konflux-requirements` (Python), `make rpm-lock` (RPMs)
- Reproduce hermetic prefetch locally: `make hermeto-prefetch`

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
