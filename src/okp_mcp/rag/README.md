# RAG Search Pipeline

This package queries the portal-rag Solr core (and its sibling portal core) to
give LLMs chunked, context-rich answers from Red Hat's knowledge base. The
logic is best understood as a pipeline, where each stage transforms data for a
specific reason.

## The Problem This Solves

The OKP Solr container holds two cores:

- **portal-rag** has docs, CVEs, and errata split into passage-sized chunks
  with 384-dimensional vector embeddings. Great for precise retrieval, but a
  single matched chunk is often too narrow for an LLM to give a useful answer.
- **portal** has flat, whole documents for solutions and articles that are
  *missing* from portal-rag entirely (157K solutions, 7K articles). Different
  schema, different query mechanics.

The RAG pipeline bridges these cores into a single search tool, handling query
cleaning, multi-strategy search, deduplication, context expansion, and output
formatting.

## Pipeline Overview

```text
LLM tool call
    |
    v
search_rag (tools.py)
    |
    +-- 1. clean query (common.py)
    |       strip stopwords, quote hyphenated compounds
    |
    +-- 2. search (hybrid.py / lexical.py / semantic.py / portal.py)
    |       hit Solr, get back typed response models
    |
    +-- 3. deduplicate (formatting.py)
    |       collapse multiple chunks from the same parent doc
    |
    +-- 4. expand context (context.py)
    |       fetch surrounding chunks so the LLM has enough to work with
    |
    +-- 5. format + budget (formatting.py, tools.py)
    |       render markdown, enforce character limit
    |
    v
string response to LLM
```

## Stage 1: Query Cleaning

Raw user queries produce poor Solr results. A question like *"How do I
configure rpm-ostree on RHEL 9?"* contains stopwords that dilute BM25
scoring, and `rpm-ostree` gets tokenized into `rpm` + `ostree` by Solr's
standard tokenizer (drowning specific results in generic RPM matches).

`clean_rag_query()` fixes this:

1. **Split quoted phrases** from plain tokens so user-quoted terms stay intact
2. **Strip stopwords** ("how", "do", "I", "on") to sharpen BM25 relevance
3. **Preserve numeric tokens** like version numbers ("9", "4.16")
4. **Quote hyphenated compounds** ("rpm-ostree" becomes `"rpm-ostree"`) to
   force Solr phrase matching
5. **Fall back** to the original query if cleaning removes everything

The cleaned query feeds into whichever search strategy runs next.

## Stage 2: Search

Four search strategies exist because no single approach covers everything.

### Hybrid Search (primary path)

The `search_rag` MCP tool always runs hybrid retrieval. It hits the
`/hybrid-search` Solr request handler, which has server-side eDisMax config
with field boosts (title^30, chunk^20, headings_txt^15), phrase boosting,
recency bias, and document-type weighting baked in. The client sends only the
query, row count, chunk filter, and an optional product boost query.

Product boosting resolves aliases ("RHEL" to "Red Hat Enterprise Linux") and
injects a `bq` parameter so product-relevant chunks score higher without
excluding cross-product results.

### Lexical Search

A simpler eDisMax query against `/select` with client-side field boosts
(title^20, chunk^10). Useful as a standalone search or as one input to
reciprocal rank fusion.

### Semantic Search

Pure KNN vector search against `/semantic-search`. Accepts either a
pre-computed 384-dimensional vector or raw text (via the `Embedder` class,
which wraps `granite-embedding-30m-english` in a thread-safe executor). The
text-to-vector path runs the embedding model asynchronously via a
`ThreadPoolExecutor` with `max_workers=1` to serialize the non-thread-safe
Rust tokenizer.

When an embedder is available in `AppContext`, `search_rag` runs semantic text
search in parallel with hybrid search, then merges both result sets using
reciprocal rank fusion. If semantic search fails, the tool logs a warning and
gracefully falls back to hybrid-only results.

### Portal Search

Solutions and articles live in the legacy **portal** core, not portal-rag.
`portal_search()` queries `/solr/portal/select` with its own eDisMax boosts
and `PortalDocument`/`PortalResponse` models. It has a separate query runner
(`_portal_query()`) to avoid coupling with the chunk-based portal-rag models.

### Result Fusion

`reciprocal_rank_fusion()` merges any two `RagResponse` sets. For each
document, it sums `1/(k + rank)` across all lists where it appears (k=60 by
default). Documents appearing in both lists naturally score higher. The output
is a single `RagResponse` sorted by fused score, with each doc carrying an
`rrf_score` field. This is a pure function with no Solr dependency; it only
reshuffles existing results.

## Stage 3: Deduplication

The portal-rag core stores documents as multiple chunks. A search for "RHEL 9
firewall" might return chunks 3, 7, and 12 from the same firewall guide.
Returning all three wastes the LLM's context window with redundant information.

`deduplicate_chunks()` fixes this by:

1. **Grouping** chunks by `parent_id`
2. **Filtering** chunks below a token threshold (default 30 tokens), since
   tiny chunks are usually boilerplate (headers, footers, nav text)
3. **Selecting** the highest-ranked chunk per parent (earliest in the Solr
   result list, i.e., most relevant)
4. **Preserving** orphan chunks (no `parent_id`) as unique results

The output is a shorter list where each parent document appears at most once,
ordered by original rank.

## Stage 4: Context Expansion

A single matched chunk is typically 200-400 tokens of text. That's enough to
*find* the right document, but often not enough for an LLM to *answer* from.
The surrounding chunks usually contain the setup, prerequisites, or follow-up
steps the LLM needs.

`expand_chunks()` runs all expansions concurrently via `asyncio.gather`:

1. **Fetch parent metadata** to get `total_chunks` and `total_tokens`
2. **Decide expansion strategy** based on document size:
   - **Small documents** (total_tokens <= 4000, covering ~93% of the corpus):
     fetch all chunks for the full document
   - **Large documents**: fetch a window of +/- 2 chunks around the match
3. **Merge** expanded chunks into a single `RagDocument`, concatenating text
   in `chunk_index` order while preserving the anchor chunk's metadata (title,
   URL, product, headings)

Errors during expansion are swallowed gracefully: the original chunk is
returned unchanged. A failed expansion never breaks the pipeline.

## Stage 5: Format and Budget

Each expanded `RagDocument` is rendered as a markdown block with title,
section breadcrumbs, product info, URL, and chunk content. Missing fields are
omitted (no "None" placeholders).

`_assemble_rag_output()` then enforces a character budget: it adds results
until the budget is exceeded, always including at least one result even if it's
over budget. If truncated, a notice tells the LLM how many results were cut.

## Data Models

Two model families live in `models.py`, reflecting the two Solr cores:

- **`RagDocument`/`RagResponse`** for portal-rag chunks. Key fields:
  `parent_id` (links chunk to source doc), `chunk` (passage text),
  `chunk_index` (ordering), `num_tokens` (size), `rrf_score` (fusion score),
  `total_chunks`/`total_tokens` (parent metadata for expansion decisions).
- **`PortalDocument`/`PortalResponse`** for portal core results. Key fields:
  `main_content` (full document body), `url_slug` (for link construction),
  `documentKind` (solution or article).

Both use `extra="allow"` so unexpected Solr fields don't break parsing.

## How It Wires Together

The `search_rag` MCP tool in `tools.py` is the only entry point exposed to
LLMs. At server startup, `_app_lifespan()` in `server.py` creates a shared
`httpx.AsyncClient` and resolves the RAG Solr URL. If `MCP_RAG_SOLR_URL`
isn't set, all tools tagged `{"rag"}` are disabled automatically.

Every search function takes the same three infrastructure args: `client`
(shared HTTP client), `solr_url` (base Solr URL), and `max_results`. No
function creates its own HTTP client or reads config directly. This makes
every component independently testable with `respx` mocks.
