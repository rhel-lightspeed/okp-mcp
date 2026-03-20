# AGENTS.md - okp-mcp

MCP server bridging LLM tool calls to a Solr-indexed Red Hat knowledge base (docs, CVEs, errata, solutions). Built on FastMCP + httpx + pydantic-settings.

## Build & Run

```bash
uv sync                          # install all deps (including dev)
uv run okp-mcp                   # run server (stdio, default)
uv run okp-mcp --transport streamable-http --port 8000  # HTTP mode
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
uv run pytest tests/test_tools.py          # single file
uv run pytest tests/test_tools.py::test_solr_query_returns_results  # single test
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

Functional tests are **deselected by default** via `pytest_collection_modifyitems` in `tests/conftest.py`. They only run when explicitly requested with `-m functional`. Credentials load from `.env` via `python-dotenv`.

**Required environment variables** (set in `.env`):
- `GOOGLE_APPLICATION_CREDENTIALS`: path to service account JSON (e.g., `./secrets/your-sa.json`)
- `GOOGLE_CLOUD_PROJECT`: GCP project ID

**Optional**:
- `OKP_FUNCTIONAL_MODEL`: Gemini model override (default: `gemini-2.5-flash`)

**Key files**:
- `tests/test_functional.py`: test runner with MCPServerStdio + GoogleProvider
- `tests/functional_cases.py`: `FunctionalCase` dataclass + parametrized test data
- `tests/fixtures/functional_system_prompt.txt`: LLM system prompt adapted for this project's tools

**Architecture notes**:
- Each test spawns a fresh MCP server subprocess with `--transport stdio` (the project defaults to `streamable-http`, so this flag is critical)
- Region is hardcoded to `us-central1`
- `temperature=0` for reproducibility
- Assertions check: tool call count, expected document references in tool returns/response, required facts (with tuple alternatives for "or" logic), and forbidden claims
- Tests skip gracefully when credentials or Solr are unavailable

## Project Layout

```
src/okp_mcp/
  __init__.py   # entry point, main(), logging config, re-exports mcp
  config.py     # ServerConfig (pydantic BaseSettings, MCP_* env vars)
  server.py     # FastMCP instance (single `mcp` object)
  tools.py      # @mcp.tool definitions (solr_query, etc.)
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # Vertex AI functional tests (gated behind -m functional)
  test_*.py            # unit test modules mirror src structure
  fixtures/
    functional_system_prompt.txt  # LLM system prompt for functional tests
docs/
  SOLR_EXPLORATION.md  # Solr schema map, field inventory, document types, query handler config, and data characteristics
```

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
- Pattern:
  ```python
  try:
      ...
  except httpx.TimeoutException:
      logger.warning("...")
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
- `podman-compose up -d` to run with Solr

## Complexity

All functions must be rated A or B by radon. C or higher fails the CI gate. Refactor until compliant.
