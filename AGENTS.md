# AGENTS.md - okp-mcp

MCP server bridging LLM tool calls to a Solr-indexed Red Hat knowledge base (docs, CVEs, errata, solutions). Built on FastMCP + httpx + pydantic-settings.

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

Functional tests use Pydantic AI + Vertex AI Gemini to verify MCP tools return correct answers for known-incorrect CLA scenarios (RSPEED Jira tickets). They spawn a real MCP server subprocess via `MCPServerStdio`, send questions through Gemini, and assert response quality.

```bash
uv run pytest -m functional -v           # run functional tests (requires live Solr + Vertex AI)
uv run pytest -m functional -k "2482"    # run a single case
```

Functional tests are **deselected by default** via `pytest_collection_modifyitems` in `tests/conftest.py`. They only run when explicitly requested with `-m functional`. Credentials are loaded exclusively from `.env` via `python-dotenv` — bare environment variables are not sufficient.

**Required** (in `.env`):
- `GOOGLE_APPLICATION_CREDENTIALS`: path to service account JSON (e.g., `./secrets/your-sa.json`)
- `GOOGLE_CLOUD_PROJECT`: GCP project ID

**Optional** (in `.env`):
- `OKP_FUNCTIONAL_MODEL`: Gemini model override (default: `gemini-2.5-flash`). Read exclusively from `.env`, not from environment variables.

**Key files**:
- `tests/test_functional.py`: test runner with MCPServerStdio + GoogleProvider
- `tests/functional_cases.py`: `FunctionalCase` dataclass + parametrized test data
- `tests/fixtures/functional_system_prompt.txt`: LLM system prompt adapted for this project's tools

**Architecture notes**:
- Each test spawns a fresh MCP server subprocess with `--transport stdio` (the project defaults to `streamable-http`, so this flag is critical)
- Region is hardcoded to `us-central1`
- `temperature=0` for reproducibility
- Assertions check: tool call count, expected document references in tool returns/response, required facts (with tuple alternatives for "or" logic), and forbidden claims
- Tests skip gracefully when `.env` is missing, credentials are invalid, or Solr is unavailable
- Tests are fully independent (each spawns its own MCP subprocess, HTTP client, and Gemini agent), so pass `-n 4` to run them in parallel via pytest-xdist

**Workflow**: See `INCORRECT_ANSWER_LOOP.md` for the full process of turning RSPEED "incorrect answer" tickets into functional test cases and fixing the MCP server until all tests pass.

## Project Layout

```text
src/okp_mcp/
  __init__.py    # entry point, main(), logging config, re-exports mcp
  config.py      # ServerConfig (pydantic BaseSettings, MCP_* env vars)
  server.py      # FastMCP instance (single `mcp` object), AppContext, lifespan
  portal.py      # Unified portal search: query builders, chunk conversion, RRF, orchestrator, formatting
  tools.py       # @mcp.tool definitions (search_portal, get_document, run_code)
  solr.py        # Solr query builder, BM25 paragraph extraction, RHV filtering
  content.py     # Boilerplate stripping, content truncation, text cleaning
  formatting.py  # Result annotation, deprecation/replacement detection, sort keys
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # Vertex AI functional tests (gated behind -m functional)
  test_portal.py       # portal.py unit tests: query builders, chunk conversion, RRF, formatting, orchestrator
  test_*.py            # unit test modules mirror src structure
  fixtures/
    functional_system_prompt.txt  # LLM system prompt for functional tests
docs/
  SOLR_EXPLORATION.md     # Historical: original redhat-okp container schema map
openshift/
  okp-mcp.yml   # OpenShift deployment template (Deployment, Service, ServiceAccount)
INCORRECT_ANSWER_LOOP.md  # step-by-step workflow for turning RSPEED "incorrect answer" tickets into functional tests and fixes
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a new MCP tool | `src/okp_mcp/tools.py` | Add `@mcp.tool` async function; follows error handling pattern |
| Change portal search logic | `src/okp_mcp/portal.py` | Query builders, chunk conversion, RRF fusion, orchestrator, formatting |
| Change Solr query logic | `src/okp_mcp/solr.py` | `_solr_query()` builds edismax params; `_clean_query()` for tokenization |
| Modify result formatting | `src/okp_mcp/formatting.py` | `_annotate_result()` for deprecation/EOL (used by portal.py) |
| Change content cleaning | `src/okp_mcp/content.py` | `strip_boilerplate()` regex, `truncate_content()` |
| Modify config/CLI args | `src/okp_mcp/config.py` | Add field to `ServerConfig`; auto-generates CLI arg via `MCP_` prefix |
| Add functional test case | `tests/functional_cases.py` | Add `FunctionalCase` to `FUNCTIONAL_TEST_CASES` list |
| Mock Solr responses | `tests/conftest.py` | `solr_mock` fixture uses respx |
| Deploy to OpenShift | `openshift/okp-mcp.yml` | Template with params: IMAGE, IMAGE_TAG, REPLICAS, etc. |
| Solr schema reference | `docs/SOLR_EXPLORATION.md` | Historical: original redhat-okp container schema map |

## Boot Sequence

```text
uv run okp-mcp [--transport ...] [--port ...]
  → pyproject.toml: okp-mcp = "okp_mcp:main"
  → __init__.py: main()
       ├─ CliApp.run(ServerConfig)     # parse CLI + MCP_* env vars
       ├─ _configure_logging()
        └─ mcp.run(transport=...)       # start FastMCP server
            → server.py: _app_lifespan()
                ├─ creates shared httpx.AsyncClient
                └─ yields AppContext(...)
            → tools.py: @mcp.tool funcs  # registered via side-effect import
```

## Module Dependencies

```text
__init__.py → config, server, tools (side-effect import)
tools.py    → config, portal, server, solr, content
portal.py   → config, content, formatting, solr
formatting.py → content, solr
solr.py     → config
server.py   → config
content.py  → (standalone)
```

No circular imports. `content.py` has zero internal dependencies.

## Code Style

### Python Version & Formatting
- **Target**: Python 3.12+ (CI tests 3.12, 3.13, 3.14)
- **Line length**: 120 characters
- **Formatter**: ruff format
- **Linter**: ruff check with rules: E, F, W, I (isort), UP, S (security), B (bugbear), A, C4, SIM

### Imports
- Order: stdlib, third-party, relative (enforced by ruff `I` rule)
- Use relative imports within the package (`from .config import ServerConfig`)
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
coderabbit review --agent --base <base-branch>
```

If findings come back, address them before creating the PR (or flag them for the user). Zero findings means good to go.

## Workarounds

- `run_code()` in tools.py is a KLUDGE: placeholder tool that prevents Gemini 2.5 Flash from crashing when it tries to use its built-in code execution tool. Returns a polite "not supported" message. Do not remove without verifying Gemini behavior first.
