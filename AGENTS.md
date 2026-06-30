# AGENTS.md — okp-mcp

MCP server bridging LLM tool calls to a Solr-indexed Red Hat knowledge base (docs, CVEs, errata, solutions). Built on FastMCP + httpx + pydantic-settings + sentry-sdk.

## Maintenance Rule

After any code change, verify that this file and the docs in `docs/` are still accurate. Update in the same PR if anything drifted.

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
make rpm-lock                    # regenerate rpms.lock.yaml from rpms.in.yaml
make hermeto-prefetch            # locally validate the hermetic prefetch (requires podman)
```

## Pre-commit Hooks

Install with `pre-commit install` (or `make setup`):

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

pytest is configured with `asyncio_mode = "auto"`. Tests are randomized via pytest-randomly.

### Functional Tests

Functional tests verify document retrieval quality by calling `_run_portal_search()` directly against a live Solr instance. Assertions target `PortalChunk` objects — deterministic, no LLM involved.

Test scenarios live in `tests/functional_cases.py` as `FunctionalCase` dataclasses. Functional tests are **deselected by default** and only run with `-m functional`. They require a running OKP Solr container (`podman-compose up -d`); tests skip automatically if Solr is unreachable.

## Where to Look

Application code lives in `src/okp_mcp/`, tests in `tests/`. See the module dependency graph below for import relationships.

Docs for build/pipeline details: `docs/CONTAINER_BUILDS.md`, `docs/TEKTON_PIPELINES.md`.

| Task | Location | Notes |
|------|----------|-------|
| Add a new MCP tool | `src/okp_mcp/tools/` | Add `@mcp.tool` async function, re-export from `tools/__init__.py` |
| Change request ID propagation or response headers | `src/okp_mcp/request_id.py` | `RequestIDContextMiddleware`, `RequestIDHeaderMiddleware`, `RequestIDLogFilter` |
| Add/modify Prometheus metrics | `src/okp_mcp/metrics.py` | Counters, histograms, `PrometheusMiddleware` ASGI class |
| Add/modify intent detection | `src/okp_mcp/intent.py` | Append `IntentRule` to `INTENT_RULES` at the correct priority position |
| Change portal search logic | `src/okp_mcp/portal.py` | Query builders, chunk conversion, RRF fusion, multi-query orchestrators |
| Change Solr query logic | `src/okp_mcp/solr.py` | `_solr_query()` builds edismax params; `_clean_query()` for tokenization |
| Modify result formatting | `src/okp_mcp/formatting.py` | `annotate_result()` for deprecation/EOL |
| Change content cleaning | `src/okp_mcp/content.py` | `strip_boilerplate()` regex, `truncate_content()` |
| Modify config/CLI args | `src/okp_mcp/config.py` | Add field to `ServerConfig`; auto-generates CLI arg and `MCP_`-prefixed env var |
| Add functional test case | `tests/functional_cases.py` | Add `FunctionalCase` to `FUNCTIONAL_TEST_CASES` list |
| Mock Solr responses | `tests/conftest.py` | `solr_mock` fixture uses respx |
| Deploy to OpenShift | `openshift/okp-mcp.yml` | Template with params: IMAGE, IMAGE_TAG, REPLICAS, etc. |
| Run locally with systemd | `quadlet/` | See `quadlet/README.md` |
| Modify pre-commit hooks | `.pre-commit-config.yaml` | Runs on every commit |
| Change hermetic build deps | `scripts/konflux_requirements.sh`, `.konflux/`, `rpms.in.yaml` | Regenerate with `make konflux-requirements` and `make rpm-lock`; CI gates drift |
| Container builds | `Containerfile`, `Containerfile-source`, `scripts/container-install.sh` | See `docs/CONTAINER_BUILDS.md` |
| Tekton pipeline maintenance | `.tekton/` | See `docs/TEKTON_PIPELINES.md` |
| Modify CI/CD workflows | `.github/workflows/` | `build.yml`, `functional.yml`, `scorecard.yml` |

## Boot Sequence

```text
uv run okp-mcp [--transport ...] [--port ...]
  → pyproject.toml: okp-mcp = "okp_mcp:main"
  → config.py: CONFIG = ServerConfig()    # singleton evaluated at import time
  → __init__.py: main()
       ├─ _configure_logging(CONFIG.log_level)
       ├─ telemetry.initialize_error_reporting(CONFIG)  # no-op without MCP_GLITCHTIP_DSN
       ├─ log version + commit SHA
       └─ mcp.run(transport=..., **CONFIG.transport_kwargs)
            → server.py: _app_lifespan()
                ├─ creates shared httpx.AsyncClient
                └─ yields AppContext(...)
            → server.py: imports tools/* for @mcp.tool registration
            → server.py: registers /metrics custom_route
```

## Module Dependencies

```text
__init__.py → build_info, config, request_id, server, telemetry
build_info.py → (standalone; reads COMMIT_SHA env var, APP_ROOT/COMMIT_SHA file, or local `git rev-parse`)
tools/__init__.py → tools/search.py, tools/document.py, tools/run_code.py
tools/search.py → metrics, portal, server
tools/document.py → content, metrics, server, solr, tools/shared.py, types
tools/run_code.py → server
metrics.py  → (standalone; pure Prometheus instrumentation)
request_id.py → fastmcp.server.dependencies, fastmcp.server.middleware, starlette
config.py   → metrics, request_id
intent.py   → config, metrics
portal.py   → config, content, formatting, intent, metrics, solr, types
formatting.py → (standalone)
solr.py     → bm25, config, metrics, types
bm25.py     → (standalone)
server.py   → config, request_id, prometheus_client, tools
telemetry.py → build_info, config, sentry_sdk
content.py  → types
types.py    → (standalone)
```

No circular imports. `types.py`, `bm25.py`, and `formatting.py` have zero internal dependencies.

## Code Style

### Python Version & Formatting
- **Target**: Python 3.12+ (CI tests 3.12, 3.13, 3.14)
- **Line length**: 120 characters
- **Formatter/Linter**: ruff (rules: E, F, W, I, UP, S, B, A, C4, SIM, TID252)

### Imports
- Order: stdlib, third-party, first-party (enforced by ruff `I` rule)
- **ZERO relative imports.** Always absolute (`from okp_mcp.config import ServerConfig`). Enforced by ruff rule `TID252`.
- Side-effect imports get a `noqa` comment: `from okp_mcp import tools  # noqa: F401 -- triggers @mcp.tool registration`

### Type Hints
- Type checker: `ty` (not mypy/pyright)
- Use `typing.Literal` for constrained string values
- Use pydantic `Field()` with descriptions for config
- Use `@computed_field` for derived config properties
- Add `# type: ignore[prop-decorator]` on computed_field + @property combos (known ty quirk)

### Naming
- `snake_case` for functions, variables, modules; `PascalCase` for classes
- Prefix unused imports with `_`; constants in `UPPER_SNAKE_CASE`

### Docstrings
- PEP 257 style on every module, class, and function (including tests and fixtures)
- Module docstrings are single-line: `"""Description of the module."""`
- Test docstrings describe the behavior being verified, not the test name

### Error Handling
- Return user-friendly strings on failure (not exceptions) for MCP tools
- Use specific exception types (`httpx.TimeoutException`, not bare `Exception`)
- Log exceptions with `logger.exception()` for stack traces
- Log warnings with `logger.warning()` and `exc_info=True`
- **Never swallow exception details**: every except block that logs MUST include traceback info
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
- pytest asyncio_mode is `auto`, no `@pytest.mark.asyncio` needed

### Security Suppressions
- `# noqa: S104` on intentional `0.0.0.0` binds with comment
- `# noqa: S101` suppressed globally in tests/ (assert usage)

## Configuration Pattern

Config uses `pydantic_settings.BaseSettings` with `MCP_` env prefix. A singleton `CONFIG = ServerConfig()` is evaluated at module import time. Precedence: CLI > env vars > defaults.

`ServerConfig` lives in `config.py`. CLI args are auto-generated from `Field()` definitions. `@computed_field` handles derived values like `solr_endpoint` and `transport_kwargs`.

`STOP_WORDS` and `logger` are module-level in `config.py` (outside the class to avoid circular imports). The Solr endpoint flows through `CONFIG.solr_endpoint` → `AppContext.solr_endpoint` at runtime.

Optional GlitchTip/Sentry exception reporting is configured with `MCP_GLITCHTIP_DSN` / `--glitchtip-dsn`. Missing or empty DSNs are handled as a no-op.

## Testing Patterns

- **HTTP mocking**: `respx` library (not `responses` or `aioresponses`)
- **Fixtures**: shared in `conftest.py`, test-local when specific
- **Parametrize**: use `@pytest.mark.parametrize` for value variations
- **Mocking**: `unittest.mock.patch` / `patch.dict` for env vars
- **Assert style**: direct assertions, `pytest.raises` for expected errors

## Container & Tekton

- Container builds: see [`docs/CONTAINER_BUILDS.md`](docs/CONTAINER_BUILDS.md)
- Tekton pipeline maintenance: see [`docs/TEKTON_PIPELINES.md`](docs/TEKTON_PIPELINES.md)

## Complexity

All functions must be rated A or B by radon. C or higher fails the CI gate. Refactor until compliant.

## Workarounds

- `run_code()` in `src/okp_mcp/tools/run_code.py` is a KLUDGE: placeholder tool that prevents Gemini 2.5 Flash from crashing when it tries to use its built-in code execution tool. Returns a polite "not supported" message. Do not remove without verifying Gemini behavior first.

## Pre-PR Code Review

Check if `coderabbit` is available in `$PATH`. If it is, ask whether they'd like a review. Run it with:

```bash
coderabbit review --agent --base <base-branch> -c .coderabbit.yaml
```

The CLI does not auto-read `.coderabbit.yaml` from the repo root. Always pass `-c .coderabbit.yaml`.
