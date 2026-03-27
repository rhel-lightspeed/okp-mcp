# AGENTS.md - okp_mcp.rag

RAG search pipeline querying the portal-rag and portal Solr cores. Handles query cleaning, multi-strategy search (hybrid/lexical/semantic/portal), deduplication, context expansion, and output formatting. See `README.md` in this directory for the full pipeline walkthrough.

For project-wide conventions (code style, CI commands, testing framework, config pattern, container builds), see the root `AGENTS.md`.

## Maintenance Rule

After any code change in this directory, verify that both `README.md` and `AGENTS.md` (this file) are still accurate and complete. Update them in the same PR if anything has drifted: new modules, changed function signatures, removed features, renamed files, etc.

## Module Layout

```text
rag/
  __init__.py       # re-exports all public symbols
  models.py         # RagDocument, RagResponse, PortalDocument, PortalResponse
  common.py         # rag_query() runner, clean_rag_query(), RAG_FL field list
  hybrid.py         # hybrid_search() via /hybrid-search handler (primary path)
  lexical.py        # lexical_search() via /select (basic eDisMax)
  semantic.py       # semantic_search() (KNN vector), semantic_text_search() (text->embed->KNN)
  embeddings.py     # Embedder class wrapping granite-embedding-30m-english
  portal.py         # portal_search() for solutions/articles from the portal core
  rrf.py            # reciprocal_rank_fusion() for merging result sets
  context.py        # Context expansion: fetch sibling chunks, merge into richer docs
  formatting.py     # deduplicate_chunks(), format_rag_result()
  tools.py          # @mcp.tool: search_rag (the only LLM-facing entry point)
  README.md         # Pipeline architecture docs (start here for understanding)
```

## Module Dependencies

```text
models.py       -> (standalone, pydantic only)
common.py       -> config (STOP_WORDS, logger), models
lexical.py      -> common (rag_query), models
hybrid.py       -> common (rag_query), models
semantic.py     -> common (rag_query), models, embeddings (TYPE_CHECKING only)
embeddings.py   -> sentence_transformers, torch (imported by server.py for AppContext init)
portal.py       -> config (logger), models
rrf.py          -> models
context.py      -> common (RAG_FL, rag_query), models
formatting.py   -> models
tools.py        -> server (mcp, get_app_context), common (RAG_FL, clean_rag_query),
                   formatting, hybrid, models
```

No circular imports. `models.py`, `rrf.py`, and `formatting.py` have no dependencies outside the subpackage. `embeddings.py` is the only module that imports ML libraries (sentence_transformers, torch).

## Where to Look

| Task | File | Notes |
|------|------|-------|
| Add/modify RAG MCP tool | `tools.py` | `@mcp.tool(tags={"rag"})`, disabled when `MCP_RAG_SOLR_URL` not set |
| Add a new search strategy | New file (e.g., `reranking.py`) | Follow `lexical.py` pattern: accept `client`, `solr_url`, `max_results`, return `RagResponse` |
| Change hybrid search boosts | Server-side | `/hybrid-search` handler config is in Solr, not here. Client only sends `q`, `rows`, `fq`, `bq` |
| Add product aliases | `hybrid.py` | `_PRODUCT_ALIASES` dict maps short names to full Solr values |
| Fix query cleaning | `common.py` | `clean_rag_query()` and helpers: `_split_quoted_and_plain`, `_quote_hyphenated_compounds` |
| Change deduplication logic | `formatting.py` | `deduplicate_chunks()` groups by `parent_id`, picks top-ranked per parent |
| Change context expansion | `context.py` | `expand_chunk()` decides full-doc vs windowed based on `total_tokens` |
| Add Solr response fields | `models.py` | Add field to `RagDocument` or `PortalDocument` (both use `extra="allow"`) |
| Change response formatting | `formatting.py` | `format_rag_result()` renders markdown blocks |
| Change output budget/assembly | `tools.py` | `_assemble_rag_output()` enforces character limit |
| Add embedding model support | `embeddings.py` | `Embedder` class, `ThreadPoolExecutor` for async |
| Add result fusion logic | `rrf.py` | Pure function, no Solr dependency |
| Query the portal core | `portal.py` | Separate models (`PortalDocument`) and query runner (`_portal_query`) |
| Solr schema reference | `../../docs/OKP_RAG_EXPLORATION.md` | Field names, vector dimensions, core differences |

## Two Solr Cores

The RAG container hosts two cores with different schemas and different content:

- **portal-rag**: Docs, CVEs, errata split into passage-sized chunks with 384-dim vector embeddings. Queried by `hybrid.py`, `lexical.py`, `semantic.py`, `context.py`.
- **portal**: Flat whole documents for solutions and articles that are _missing_ from portal-rag (157K solutions, 7K articles). Queried by `portal.py` with its own models and query runner.

These are NOT interchangeable. `RagDocument`/`RagResponse` are for portal-rag, `PortalDocument`/`PortalResponse` are for portal.

## Function Signature Convention

All search functions follow the same pattern for infrastructure args:

```python
async def some_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
    fl: str | None = None,
) -> RagResponse:
```

- `client`: shared `httpx.AsyncClient` from `AppContext` (never create your own)
- `solr_url`: base Solr URL, no trailing slash
- `max_results`: row limit
- `fl`: Solr field list (optional, defaults to Solr handler config)
- Returns typed response model (`RagResponse` or `PortalResponse`)

No function reads config directly or creates its own HTTP client. This makes every component independently testable with `respx` mocks.

## Error Handling

Follows the project-wide pattern from root `AGENTS.md` (tools return user-friendly strings, internals raise). Two RAG-specific details:

- **Internal functions raise**: `rag_query()` and `_portal_query()` propagate `httpx` exceptions. Only the `@mcp.tool` in `tools.py` catches and converts them to strings.
- **Context expansion is resilient**: `expand_chunk()` catches `httpx.HTTPError` internally and returns the original chunk unchanged, so a failed expansion never breaks the pipeline.

## Testing

Tests live in `tests/rag/` and mirror the module structure. See root `AGENTS.md` for framework choices (respx, parametrize, asyncio_mode, etc.).

```bash
uv run pytest tests/rag/                        # all RAG tests
uv run pytest tests/rag/test_hybrid.py           # single module
uv run pytest tests/rag/test_hybrid.py::test_hybrid_search_sends_edismax_params  # single test
```

RAG-specific details:
- **Shared fixtures**: `tests/rag/conftest.py` has `rag_chunk_response` (realistic Solr JSON) and `rag_client` (async httpx client with cleanup)
- **Functional tests**: `test_context_functional.py` and `test_search_functional.py` are gated behind `-m functional` (need live Solr)

When adding a new search function, add a corresponding `test_<module>.py` that covers:
1. Correct Solr endpoint and params
2. Response parsing into typed models
3. Empty/error response handling
4. Edge cases (empty query, missing fields)

## Gotchas

- **`is_chunk:true` filter**: Most portal-rag queries need `fq=is_chunk:true` to exclude parent documents. Context expansion (`context.py`) uses `is_chunk:false` to fetch parent metadata. Mixing these up returns wrong results silently.
- **Product boost vs filter**: `hybrid_search()` uses `bq` (boost query) for product, not `fq` (filter query). This raises product-relevant results without excluding cross-product matches.
- **Solr injection**: `hybrid.py` strips `\` and `"` from product names before interpolating into `bq`. Any new user-controlled string interpolated into Solr params needs the same treatment.
- **Embedder thread safety**: The Rust tokenizer in sentence-transformers is not thread-safe. `Embedder` uses `max_workers=1` and a `threading.Lock` to serialize calls. Do not increase max_workers.
- **`extra="allow"` on models**: Both `RagDocument` and `PortalDocument` accept extra fields from Solr without error. This means typos in field names won't raise, they'll just be silently ignored.
- **RAG tools disabled at startup**: If `MCP_RAG_SOLR_URL` is not set, all tools tagged `{"rag"}` are disabled automatically. Check `server.py` for the gating logic.
- **Portal core has its own query runner**: `portal.py` uses `_portal_query()` instead of `rag_query()` from `common.py`. They look nearly identical but return different types (`PortalResponse` vs `RagResponse`). Do not unify them without also unifying the models.
- **`AppContext.embedder` may be None**: The Embedder is initialized at server startup in `server.py` and stored in `AppContext.embedder`. It will be `None` if RAG is disabled (`MCP_RAG_SOLR_URL` not set) or if the embedding model failed to load. Always check `app.embedder is not None` before calling `encode()` or `encode_async()`.
