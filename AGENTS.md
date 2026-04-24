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
make ci          # full suite: lint + typecheck + radon + test
make lint        # ruff check src/ tests/
make format      # ruff format src/ tests/
make typecheck   # ty check src/
make radon       # cyclomatic complexity gate (A/B only, C+ fails)
make test        # pytest with coverage
```

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
  content.py     # Boilerplate stripping, content truncation, text cleaning
  formatting.py  # Result annotation, deprecation/replacement detection, sort keys
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # functional test runner: calls _run_portal_search() against live Solr, asserts on PortalChunk results
  test_portal.py       # portal.py unit tests: query builders, chunk conversion, RRF, formatting, single/multi-query orchestrators
  test_*.py            # unit test modules mirror src structure
.github/
  CODEOWNERS               # PR review assignment (@rhel-lightspeed/developers)
  workflows/
    build.yml              # CI/CD: lint, typecheck, radon, pytest matrix, container build+push
    functional.yml         # Functional tests against live Solr (triggered after build.yml)
    scorecard.yml          # OpenSSF Scorecard: security posture, weekly + push-to-main
docs/
  SOLR_EXPLORATION.md     # Historical: original redhat-okp container schema map
openshift/
  okp-mcp.yml   # OpenShift deployment template (Deployment, Service, ServiceAccount)
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
| Run locally with systemd | `quadlet/` | Rootless quadlet files: `.container`, `.network`, `.volume`; see `quadlet/README.md` |
| Modify CI/CD workflows | `.github/workflows/` | `build.yml` (test+container), `functional.yml` (Solr integration), `scorecard.yml` (OpenSSF) |
| Solr schema reference | `docs/SOLR_EXPLORATION.md` | Historical: original redhat-okp container schema map |

## Boot Sequence

```text
uv run okp-mcp [--transport ...] [--port ...]
  → pyproject.toml: okp-mcp = "okp_mcp:main"
  → __init__.py: main()
       ├─ CliApp.run(ServerConfig)     # parse CLI + MCP_* env vars
       ├─ _configure_logging()
       ├─ telemetry.initialize_error_reporting()  # no-op unless MCP_GLITCHTIP_DSN is set
       ├─ log version + commit SHA     # build_info.py reads /app/COMMIT_SHA
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
build_info.py → (standalone, reads ./COMMIT_SHA file)
tools/__init__.py → tools/search.py, tools/document.py, tools/run_code.py
tools/search.py → config, metrics, portal, server
tools/document.py → content, metrics, server, solr, tools/shared.py
tools/run_code.py → config, server
metrics.py  → server (imports mcp for custom_route)
request_id.py → fastmcp.server.dependencies, fastmcp.server.middleware, starlette
intent.py   → config
portal.py   → config, content, formatting, intent, solr
formatting.py → content, solr
solr.py     → config, metrics
server.py   → config
telemetry.py → build_info, config, sentry_sdk
content.py  → (standalone)
```

No circular imports. `content.py` has zero internal dependencies.

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
- Multi-stage build: UBI 10 builder + minimal UBI 10 Python 3.12 runtime
- `podman-compose up -d` to run with Solr (`rhokp-rhel9` from `registry.redhat.io`)

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
