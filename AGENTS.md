# AGENTS.md - okp-mcp

MCP server bridging LLM tool calls to a Solr-indexed Red Hat knowledge base (docs, CVEs, errata, solutions). Built on FastMCP + httpx + pydantic-settings.

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

**Workflow**: See `INCORRECT_ANSWER_LOOP.md` for the full process of turning RSPEED "incorrect answer" tickets into functional test cases and fixing the MCP server until all tests pass.

## Project Layout

```text
src/okp_mcp/
  __init__.py    # entry point, main(), logging config, re-exports mcp
  config.py      # ServerConfig (pydantic BaseSettings, MCP_* env vars)
  server.py      # FastMCP instance (single `mcp` object), AppContext, lifespan
  tools.py       # @mcp.tool definitions (search_*, get_document, run_code)
  solr.py        # Solr query builder, BM25 paragraph extraction, RHV filtering
  content.py     # Boilerplate stripping, content truncation, text cleaning
  formatting.py  # Result annotation, deprecation/replacement detection, sort keys
  rag/           # Query functions for portal-rag Solr core
    __init__.py  # re-exports: RagDocument, RagResponse, lexical_search, hybrid_search, semantic_search, semantic_text_search, reciprocal_rank_fusion
    models.py    # RagDocument + RagResponse Pydantic models for typed Solr responses
    common.py    # lightweight Solr query runner, returns RagResponse
    lexical.py   # lexical_search() via /select (basic eDisMax)
    hybrid.py    # hybrid_search() via /hybrid-search (server-side boosted eDisMax)
    semantic.py  # semantic_search() (KNN vector) + semantic_text_search() (text -> embed -> KNN)
    embeddings.py  # Embedder class: text-to-vector via granite-embedding-30m-english (ThreadPoolExecutor)
    rrf.py          # reciprocal_rank_fusion() for merging lexical + semantic result sets
tests/
  conftest.py          # shared fixtures (solr mocks, sample responses) + functional marker deselection
  functional_cases.py  # FunctionalCase dataclass + parametrized RSPEED test data
  test_functional.py   # Vertex AI functional tests (gated behind -m functional)
  test_*.py            # unit test modules mirror src structure
  rag/                 # tests for src/okp_mcp/rag/ subpackage
    conftest.py        # RAG-specific fixtures (rag_chunk_response, rag_client)
    test_common.py     # rag_query() HTTP handling, error paths, logging
    test_embeddings.py # Embedder construction, encode, encode_async, cleanup
    test_hybrid.py     # hybrid_search() params, endpoint, response parsing
    test_lexical.py    # lexical_search() eDisMax params, chunk filter, defaults
    test_models.py     # RagDocument + RagResponse construction, extras, equality
    test_rrf.py        # reciprocal_rank_fusion() merging, scoring, edge cases
    test_semantic.py   # semantic_search() KNN, dimension validation, text search
  fixtures/
    functional_system_prompt.txt  # LLM system prompt for functional tests
docs/
  OKP_RAG_EXPLORATION.md  # RAG container research: portal + portal-rag cores, vector embeddings, schema comparison
  SOLR_EXPLORATION.md     # Historical: original redhat-okp container schema map (superseded by OKP_RAG_EXPLORATION.md)
openshift/
  okp-mcp.yml   # OpenShift deployment template (Deployment, Service, ServiceAccount)
INCORRECT_ANSWER_LOOP.md  # step-by-step workflow for turning RSPEED "incorrect answer" tickets into functional tests and fixes
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Add a new MCP tool | `src/okp_mcp/tools.py` | Add `@mcp.tool` async function; follows error handling pattern |
| Change Solr query logic | `src/okp_mcp/solr.py` | `_solr_query()` builds edismax params; `_clean_query()` for tokenization |
| Modify result formatting | `src/okp_mcp/formatting.py` | `_format_result()` + `_annotate_result()` for deprecation/EOL |
| Change content cleaning | `src/okp_mcp/content.py` | `strip_boilerplate()` regex, `truncate_content()` |
| Modify config/CLI args | `src/okp_mcp/config.py` | Add field to `ServerConfig`; auto-generates CLI arg via `MCP_` prefix |
| Add functional test case | `tests/functional_cases.py` | Add `FunctionalCase` to `FUNCTIONAL_TEST_CASES` list |
| Mock Solr responses | `tests/conftest.py` | `solr_mock` fixture uses respx; RAG fixtures in `tests/rag/conftest.py` |
| Deploy to OpenShift | `openshift/okp-mcp.yml` | Template with params: IMAGE, IMAGE_TAG, REPLICAS, etc. |
| Solr schema reference | `docs/OKP_RAG_EXPLORATION.md` | RAG container cores, vector embeddings, schema comparison |
| Legacy Solr reference | `docs/SOLR_EXPLORATION.md` | Historical: original redhat-okp container schema map |
| Add a RAG query function | `src/okp_mcp/rag/` | One file per search type; `common.py` for the shared query runner |
| Add embedding model | `src/okp_mcp/rag/embeddings.py` | Embedder class, ThreadPoolExecutor for async |
| Add search fusion | `src/okp_mcp/rag/rrf.py` | reciprocal_rank_fusion(), pure function |
| Use typed Solr response models | `src/okp_mcp/rag/models.py` | `RagDocument` (extra fields allowed) + `RagResponse` (num_found + docs) |
| Change RAG query execution | `src/okp_mcp/rag/common.py` | `rag_query()` handles HTTP, JSON parsing, and error handling |

## Boot Sequence

```text
uv run okp-mcp [--transport ...] [--port ...]
  → pyproject.toml: okp-mcp = "okp_mcp:main"
  → __init__.py: main()
      ├─ CliApp.run(ServerConfig)     # parse CLI + MCP_* env vars
      ├─ _configure_logging()
      └─ mcp.run(transport=...)       # start FastMCP server
          → server.py: _app_lifespan()  # creates shared httpx.AsyncClient
          → tools.py: @mcp.tool funcs  # registered via side-effect import
```

## Module Dependencies

```text
__init__.py → config, server, tools (side-effect import)
tools.py    → config, server, solr, content, formatting
formatting.py → content, solr
solr.py     → config
content.py  → (standalone)
rag/models.py     → (standalone, pydantic only)
rag/common.py     → config (logger only), rag.models
rag/lexical.py    → rag.common, rag.models
rag/hybrid.py     → rag.common, rag.models
rag/semantic.py   → rag.common, rag.models, rag.embeddings (TYPE_CHECKING only, not at runtime)
rag/embeddings.py → sentence_transformers, torch (isolated here only)
rag/rrf.py        → rag.models
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

Module-level constant `STOP_WORDS` lives in `config.py` outside the class to avoid circular import issues. The Solr endpoint is no longer a module-level constant — it flows through `ServerConfig.solr_endpoint` → `AppContext.solr_endpoint` at runtime.

Two new embedding fields: `embedding_model` (default: `"ibm-granite/granite-embedding-30m-english"`) and `embedding_cache_dir` (default: `None`). Available as `MCP_EMBEDDING_MODEL` and `MCP_EMBEDDING_CACHE_DIR` env vars. Read by `Embedder` callers, not wired into `AppContext` yet.

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
- `podman-compose up -d` to run with Solr (default: `rhokp-rhel9` from `registry.redhat.io`; RAG variant on port 8984 from `images.paas.redhat.com`)
- Embedding model (`ibm-granite/granite-embedding-30m-english`) is pre-cached in the builder stage via `huggingface_hub.snapshot_download()` to `/build/models`, then copied to `/app/models` in the runtime image
- `HF_HUB_CACHE=/app/models` points sentence-transformers to the cached model; `HF_HUB_OFFLINE=1` prevents network calls at runtime

## Complexity

All functions must be rated A or B by radon. C or higher fails the CI gate. Refactor until compliant.

## Workarounds

- `run_code()` in tools.py is a KLUDGE: placeholder tool that prevents Gemini 2.5 Flash from crashing when it tries to use its built-in code execution tool. Returns a polite "not supported" message. Do not remove without verifying Gemini behavior first.
