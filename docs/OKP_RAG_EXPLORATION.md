# OKP RAG Container Research: `rhokp-rag` with Vector Embeddings

Research conducted against `http://localhost:8984/solr/`, the `redhat-okp-rag` container (image: `images.paas.redhat.com/offline-kbase/rhokp-rag:mar-9-2026`). This container is the successor to the original `redhat-okp` container, adding RAG chunks with vector embeddings for semantic search alongside the existing lexical search.

## Container Overview

The RAG container ships **two Solr cores** on a single Solr 9.9.0 instance:

| Property | `portal` (legacy) | `portal-rag` (new) |
|---|---|---|
| Documents | 596,680 | 1,007,743 |
| Index size | 3.08 GB | 2.11 GB |
| Segments | 1 | 20 |
| Schema version | mimir-config v1.6 | mimir-config v1.6 |
| Unique key | `resourceName` | `resourceName` |
| Last modified | 2026-03-09 | 2026-03-09 |
| Vector support | No | Yes (384-dim cosine) |

| Infrastructure | Value |
|---|---|
| Solr version | 9.9.0 |
| Lucene version | 9.12.2 |
| Java | 21.0.10+7-LTS |
| JVM heap | 1 GB |
| Port (container) | 8983 |
| Port (host mapping) | 8984 |

The `portal` core is effectively the same schema and data as the original `redhat-okp` container (documented in `SOLR_EXPLORATION.md`), with slightly newer data (596K vs 583K docs). The `portal-rag` core is entirely new.

## `portal-rag` Core: Document Model

The RAG core uses a **parent-chunk architecture**. Each source document is split into two types of records:

### Parent Documents (63,067 records)

Metadata-only records that track the original document. No chunk text, no embeddings.

```json
{
  "doc_id": "/security/cve/CVE-2024-42225",
  "id": "/security/cve/CVE-2024-42225",
  "resourceName": "/security/cve/CVE-2024-42225",
  "title": "CVE-2024-42225 - Red Hat Customer Portal",
  "is_chunk": false,
  "is_vectorized": false,
  "total_chunks": 19,
  "total_tokens": 2698
}
```

### Chunk Documents (944,676 records)

Individual text chunks with vector embeddings. Each chunk belongs to exactly one parent via `parent_id`.

```json
{
  "doc_id": "/security/cve/CVE-2024-42225_chunk_2",
  "id": "/security/cve/CVE-2024-42225_chunk_2",
  "resourceName": "/security/cve/CVE-2024-42225_chunk_2",
  "parent_id": "/security/cve/CVE-2024-42225",
  "title": "CVE-2024-42225 - Red Hat Customer Portal",
  "chunk": "A potential flaw was found in the Linux kernel's MediaTek WiFi...",
  "chunk_index": 2,
  "num_tokens": 49,
  "headings": "CVE-2024-42225,Description",
  "online_source_url": "https://access.redhat.com/security/cve/cve-2024-42225",
  "source_path": "/security/cve/cve-2024-42225",
  "documentKind": "unknown",
  "is_chunk": true,
  "is_vectorized": true,
  "locale": "en-us",
  "size": "medium"
}
```

Key relationship: `parent.doc_id == chunk.parent_id`. A parent's `total_chunks` count matches the number of chunk records with that `parent_id`.

## Document Types (by `documentKind`)

The RAG core uses a simplified type system compared to the old `portal` core:

| documentKind | Chunk Count | Parent Count | Actual Content |
|---|---|---|---|
| `unknown` | 518,578 | ~21,039 | CVEs (~16,804 parents), documentation (~4,235 parents) |
| `errata` | 426,098 | ~42,028 | Errata/advisories (RHSA, RHBA, RHEA) |
| _(not set)_ | - | 63,067 | Parent docs only (no documentKind on parents) |

The `unknown` kind conflates what the old core separated into `Cve`, `solution`, `article`, and `documentation`. The actual document type can be inferred from `source_path`:

| `source_path` prefix | Old `documentKind` | Count (parents) |
|---|---|---|
| `/security/cve/` | `Cve` | ~16,804 |
| `/en/documentation/` | `documentation` | ~4,235 |
| `/support/` | `solution` | rare |

Notable absences vs the old `portal` core: no `solution` (156K in old), no `article` (7K in old), no `PortalProduct` (3.5K in old). The RAG core covers only CVEs, errata, and product documentation.

### Content Coverage Comparison

| Category | Old `portal` core | New `portal-rag` core |
|---|---|---|
| CVEs | 318,308 docs | ~16,804 parent + ~345K chunks |
| Errata | 92,942 docs | ~42,028 parent + ~426K chunks |
| Documentation | 16,734 docs | ~4,235 parent + ~237K chunks |
| Solutions | 157,423 docs | Not included |
| Articles | 7,384 docs | Not included |
| PortalProduct | 3,726 docs | Not included |
| CMS/Blog | 163 docs | Not included |

## Schema: Fields

### Chunk Content Fields

| Field | Type | Stored | Indexed | Multi | Notes |
|---|---|---|---|---|---|
| `chunk` | text_general | yes | yes | no | The chunk text content. Copied to `all_content`. |
| `chunk_vector` | knn_vector | **no** | yes | no | 384-dim dense vector (cosine similarity). Not stored, query-only. |
| `chunk_index` | pint | yes | yes | no | 0-based position within parent document. |
| `num_tokens` | pint | yes | yes | no | Token count for this chunk. |
| `headings` | string | yes | yes | no | Comma-separated heading hierarchy for context. Copied to `all_content` and `headings_txt`. |
| `headings_txt` | text_general | **no** | yes | no | Search-only copy of `headings` with text analysis. |

### Identity and Relationship Fields

| Field | Type | Stored | Indexed | Multi | Notes |
|---|---|---|---|---|---|
| `resourceName` | string | yes | yes | no | **Unique key.** `{parent_path}_chunk_{N}` for chunks, `{parent_path}` for parents. |
| `id` | string | yes | yes | no | Same as `resourceName`. Required. |
| `doc_id` | string | yes | yes | no | Same as `id`. Required. |
| `parent_id` | string | yes | yes | no | Links chunk to its parent document. Only on chunks. |
| `documentKind` | string | yes | yes | no | `"unknown"` or `"errata"`. Copied to `all_content`. Only on chunks. |

### Document Metadata Fields

| Field | Type | Stored | Indexed | Multi | Notes |
|---|---|---|---|---|---|
| `title` | text_general | yes | yes | no | Page title (same across all chunks of a parent). Copied to `all_content`. |
| `product` | string | yes | yes | yes | Product name(s). Multi-valued. Copied to `all_content` and `product_txt`. Only on documentation chunks. |
| `product_version` | string | yes | yes | no | Product version. Only on documentation chunks. |
| `product_txt` | text_product_synonym | **no** | yes | yes | Search-only copy with synonym expansion. |
| `online_source_url` | string | yes | yes | no | Full URL to source page. Only on chunks. |
| `source_path` | string | yes | yes | no | Path component of the source URL. Only on chunks. |
| `locale` | string | yes | yes | no | Always `"en-us"`. Only on chunks. |
| `size` | string | yes | yes | no | Always `"medium"`. Only on chunks. |

### Parent-Only Fields

| Field | Type | Stored | Indexed | Notes |
|---|---|---|---|---|
| `total_chunks` | pint | yes | yes | Number of chunks this document was split into. |
| `total_tokens` | pint | yes | yes | Total token count across all chunks. |

### Boolean Discriminator Fields

| Field | Type | Stored | Notes |
|---|---|---|---|
| `is_chunk` | boolean | yes | `true` for chunks, `false` for parents. |
| `is_vectorized` | boolean | yes | `true` for chunks, `false` for parents. Always matches `is_chunk`. |

### Timestamp Fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `lastModifiedDate` | pdate | `NOW` | All set to indexing timestamp (2026-03-09). Not the original document's modification date. |
| `timestamp` | pdate | `NOW` | Same as `lastModifiedDate`. |

### Defined But Unpopulated

| Field | Type | Notes |
|---|---|---|
| `category` | string | Schema-defined, zero documents populated. |
| `category_description` | string | Schema-defined, zero documents populated. |
| `heading_id_pairs` | string | Schema-defined, zero documents populated. |

## Schema: Vector Field Configuration

```
Field:       chunk_vector
Type:        knn_vector (solr.DenseVectorField)
Dimensions:  384
Similarity:  cosine
Stored:      no (query-only, not retrievable)
Indexed:     yes
```

Uses Solr 9's native `DenseVectorField` with HNSW indexing (Lucene's default for KNN). The 384 dimensions match the output of `ibm-granite/granite-embedding-30m-english`.

## Schema: Copy Fields

```text
chunk          --> all_content
documentKind   --> all_content
headings       --> all_content, headings_txt
product        --> all_content, product_txt
title          --> all_content
```

`all_content` aggregates all textual fields for broad lexical search using `text_en` analysis (stopwords + word delimiter + minimal English stemming + synonym expansion on query side). `product_txt` enables product name synonym expansion via `common-product-name-synonyms.txt`.

## Schema: Field Types

| Type Name | Solr Class | Analysis Chain |
|---|---|---|
| `knn_vector` | DenseVectorField | 384-dim cosine similarity |
| `text_general` | TextField | Standard tokenizer + stopwords + word delimiter + lowercase + possessive + Porter stemming + dedup |
| `text_en` | TextField | Standard tokenizer + stopwords + word delimiter + lowercase + possessive + minimal English stemming + dedup. Synonyms on query side. |
| `text_product_synonym` | TextField | Whitespace tokenizer + lowercase + stopwords + product synonym expansion + dedup |
| `string` | StrField | Exact match, sortable, doc values |
| `pint` | IntPointField | Point-based integer with doc values |
| `pdate` | DatePointField | Point-based date with doc values |

## Request Handlers (Search Endpoints)

### `/select` - Pure Lexical Search

Basic eDisMax with minimal configuration. No boosting, no recency weighting.

```text
defType:  edismax
qf:       title^20 chunk^10
rows:     10
```

No phrase boosting, no minimum match, no highlighting. The simplest of the three handlers.

### `/hybrid-search` - Boosted Lexical Search

Full-featured eDisMax with phrase boosting, recency bias, and document-type weighting. This is the richest lexical handler.

**Query fields (qf) with boosts:**

| Field | Boost | Purpose |
|---|---|---|
| `title` | ^30 | Title matches rank highest |
| `chunk` | ^20 | Chunk body text |
| `headings_txt` | ^15 | Section heading matches |
| `product_txt` | ^10 | Product name (with synonym expansion) |
| `all_content` | ^5 | Catch-all aggregate |

**Phrase boosting:**

| Parameter | Fields | Purpose |
|---|---|---|
| `pf` | title^80 chunk^60 headings_txt^40 | Exact phrase boost |
| `pf2` | title^30 chunk^20 headings_txt^15 | Bigram phrase boost |
| `pf3` | title^20 chunk^15 headings_txt^10 | Trigram phrase boost |
| `ps` / `ps2` / `ps3` | 2 / 1 / 1 | Phrase slop (word gap tolerance) |

**Minimum match (mm):** `2<-1 4<75%`
- 1-2 terms: all required
- 3 terms: all minus 1
- 4+ terms: 75% required

**Scoring modifiers:**

| Parameter | Value | Effect |
|---|---|---|
| `tie` | 0.1 | 10% contribution from non-best-matching fields |
| `bf` | `recip(ms(NOW,lastModifiedDate),3.16e-11,1,1)^5` | Recency boost (newer docs score higher) |
| `boost` | `mul(if(query($kind_errata),0.3,1), if(query($kind_documentation),1.5,1))` | Errata penalized to 0.3x, documentation boosted to 1.5x |

**Default field list (fl):** parent_id, product, resourceName, title, headings, score, documentKind, chunk_index

This handler is designed for the MCP server's primary search flow: lexical queries that favor documentation over errata noise.

### `/semantic-search` - Vector Search Shell

Minimal handler with no query parsing logic. Intended as the target for KNN vector queries where the MCP server constructs the `{!knn}` query locally.

```text
q:     *:*
rows:  10
fl:    doc_id, title, headings, heading_id_pairs, chunk_index, chunk, num_tokens,
       total_tokens, total_chunks, online_source_url, source_path, size, locale,
       category, category_description, score
```

No defType, no qf, no boosting. The MCP server is expected to:
1. Embed the user query via `granite-embedding-30m-english`
2. Send `q={!knn f=chunk_vector topK=N}[vector...]`
3. Optionally add `fq` filters for document type, product, etc.

## Chunk Statistics

### Token Distribution (944,676 chunks)

| Metric | Value |
|---|---|
| Min tokens | 6 |
| Max tokens | 512 |
| Mean tokens | 180.6 |
| Std dev | 165.7 |

| Token Range | Count | Percentage |
|---|---|---|
| 0-50 | 285,050 | 30.2% |
| 50-100 | 138,439 | 14.7% |
| 100-150 | 91,968 | 9.7% |
| 150-200 | 88,499 | 9.4% |
| 200-250 | 68,912 | 7.3% |
| 250-300 | 52,534 | 5.6% |
| 300-350 | 34,347 | 3.6% |
| 350-400 | 23,657 | 2.5% |
| 400-450 | 26,714 | 2.8% |
| 450-500 | 74,959 | 7.9% |
| 500-512 | 59,597 | 6.3% |

The max of 512 tokens matches the embedding model's sequence length limit. ~30% of chunks are very short (under 50 tokens), typically titles or single-line metadata. The bimodal spike at 450-512 tokens suggests the chunker fills to near-capacity when content is available.

### Document Size Distribution (63,067 parents)

| Metric | Value |
|---|---|
| Min chunks/doc | 1 |
| Max chunks/doc | 1,647 |
| Mean chunks/doc | 15.0 |
| Std dev | 30.2 |

| Chunks per Doc | Parent Count | Percentage |
|---|---|---|
| 1-10 | 32,151 | 51.0% |
| 11-20 | 27,411 | 43.5% |
| 21-50 | 2,198 | 3.5% |
| 51-100 | 643 | 1.0% |
| 100+ | 664 | 1.1% |

94.5% of documents produce 20 or fewer chunks. The largest document (MicroShift API Reference) has 1,647 chunks / 551K tokens.

### Largest Documents

| Document | Chunks | Tokens |
|---|---|---|
| Red Hat build of MicroShift 4.21 - Core APIs | 1,647 | 551,071 |
| Red Hat build of Debezium 3.2.6 - Source connectors | 1,536 | 568,083 |
| Red Hat build of Apache Camel 4.14 - Quarkus CXF | 1,247 | 553,822 |

## Product Coverage (Documentation Chunks Only)

Only documentation-type chunks have the `product` field populated (237,293 chunks out of 944,676 total). No errata chunks have `product` set.

Top 10 products by chunk count:

| Product | Chunks |
|---|---|
| OpenShift Container Platform | 36,373 |
| Red Hat Enterprise Linux | 12,793 |
| Red Hat build of Apache Camel | 12,218 |
| Red Hat OpenStack Platform | 10,544 |
| Red Hat OpenStack Services on OpenShift | 9,297 |
| Red Hat Fuse | 6,507 |
| Red Hat build of MicroShift | 6,455 |
| Red Hat OpenShift Service on AWS (classic) | 6,310 |
| Red Hat OpenShift Service on AWS | 6,028 |
| Red Hat JBoss Operations Network | 5,837 |

Top product versions: 4.21 (43K chunks), 10 (17K), 4 (16K), 7.13 (14K), 4.14 (12K).

## URL Patterns

Chunks provide `online_source_url` for linking back to the source page:

| Content Type | URL Pattern | Example |
|---|---|---|
| CVEs | `https://access.redhat.com/security/cve/{cve-id}` | `https://access.redhat.com/security/cve/cve-2024-42225` |
| Errata | `https://access.redhat.com/errata/{advisory-id}` | `https://access.redhat.com/errata/RHBA-2025:0929` |
| Documentation | `https://docs.redhat.com/en/documentation/{path}` | `https://docs.redhat.com/en/documentation/openshift_container_platform/4.21/html/...` |

All 944,676 chunks have `online_source_url` populated. Note that documentation URLs use `docs.redhat.com` while CVEs and errata use `access.redhat.com`.

## Embedding Model: `ibm-granite/granite-embedding-30m-english`

| Property | Value |
|---|---|
| Parameters | 30 million |
| Architecture | Encoder-only (RoBERTa-like), 6 layers, 12 attention heads |
| Output dimensions | 384 |
| Max sequence length | 512 tokens |
| Similarity function | Cosine |
| Pooling strategy | CLS token |
| Model size | ~30 MB (BF16) |
| Language | English only |
| License | Apache 2.0 |
| MTEB Retrieval (BEIR) | 49.1 (15 datasets averaged) |

The model is optimized for speed and small footprint (2x faster inference than comparably-dimensioned models). It trades some retrieval accuracy vs larger models (bge-small-en-v1.5 scores 53.86 on MTEB) but excels at multi-turn conversational retrieval (52.33 vs 38.26 for bge-small on MT-RAG benchmark).

The 512-token sequence limit aligns with the observed chunk token distribution: the chunking pipeline caps at 512 tokens to match the model's capacity.

## Query Performance

All three endpoints respond in sub-2ms for typical queries (after JVM warmup):

| Endpoint | Typical QTime | Notes |
|---|---|---|
| `/select` | 0-1ms | Pure lexical, minimal config |
| `/hybrid-search` | 1-2ms | Full eDisMax with phrase boosting + recency |
| `/semantic-search` (KNN) | 0-1ms | Vector similarity via HNSW index |

Cold-start queries (first query after restart) take 100-300ms due to index warming.

## Practical Search Patterns

### Lexical Search (keyword matching)

```text
GET /solr/portal-rag/hybrid-search?q=firewalld+RHEL+9&rows=10&wt=json
```

Uses the boosted eDisMax handler. Documentation chunks are upweighted 1.5x, errata penalized to 0.3x. Phrase proximity and recency both contribute to ranking.

### Lexical Search with Filters

```text
GET /solr/portal-rag/hybrid-search?q=configuring+SELinux&fq=product:"red hat enterprise linux"&fq=documentKind:unknown&rows=10&wt=json
```

Filter queries work on all handlers. Use `fq=is_chunk:true` to exclude parent docs from results if needed.

### Vector Search (semantic similarity)

```text
GET /solr/portal-rag/semantic-search?q={!knn f=chunk_vector topK=10}[0.01,0.02,...384 floats...]&fq=is_chunk:true&wt=json
```

The MCP server must:
1. Embed the user query using `granite-embedding-30m-english` to get a 384-dim vector
2. Format as `{!knn f=chunk_vector topK=N}[vector]`
3. Send to `/semantic-search`

### Reassembling Parent Context

To retrieve all chunks for a parent document (e.g., after finding a relevant chunk):

```text
GET /solr/portal-rag/select?q=parent_id:"/security/cve/CVE-2024-42225"&sort=chunk_index+asc&rows=100&fl=chunk,chunk_index,headings,num_tokens&wt=json
```

The `headings` field on each chunk provides section hierarchy context (comma-separated path like `"CVE-2024-42225,Description"`) so the MCP server can reconstruct which section a chunk belongs to without fetching the full document.

### Combining Lexical + Vector (Application-Level Hybrid)

Solr 9.9 doesn't natively fuse lexical and KNN scores in a single query. True hybrid search requires the MCP server to:
1. Run a lexical query via `/hybrid-search`
2. Run a KNN query via `/semantic-search`
3. Merge and re-rank results (e.g., reciprocal rank fusion)

This is an application-level concern, not a Solr configuration.

## Key Differences from Old `portal` Core

| Aspect | Old `portal` | New `portal-rag` |
|---|---|---|
| Content model | Whole documents | Parent + chunk architecture |
| Search | Lexical only (eDisMax) | Lexical + vector (KNN) |
| Text field | `main_content` (full body) | `chunk` (passage-sized) |
| Heading tracking | `heading_h1`, `heading_h2` (multi-valued) | `headings` (comma-separated hierarchy per chunk) |
| Document types | 11 distinct `documentKind` values | 2 (`unknown`, `errata`) + parents |
| Content scope | CVEs, errata, solutions, articles, docs, products | CVEs, errata, docs only |
| Product field | `product` (single string, docs only) | `product` (multi-valued string, docs only) |
| URL field | `view_uri` (path only) | `online_source_url` (full URL) |
| Errata metadata | Rich (advisory_type, severity, synopsis, product_names, product_filter) | Minimal (just `documentKind:"errata"` + chunk text) |
| CVE metadata | Structured (cve_details, threatSeverity, publicDate) | Unstructured (severity/details are in chunk text) |
| Vector embeddings | None | 384-dim cosine (granite-30m-english) |
| Synonym support | `syn_product` (text_product_synonym) | `product_txt` (text_product_synonym) |

## Implications for MCP Server

### What Changes

1. **New core name**: queries go to `/solr/portal-rag/` instead of `/solr/portal/`
2. **Chunk-level results**: search returns individual chunks, not whole documents. The MCP server needs to decide whether to return raw chunks or reassemble parent context.
3. **No structured metadata on chunks**: errata severity, CVE threat level, advisory type - these are now embedded in chunk text rather than discrete fields. Filtering by severity requires text matching, not field queries.
4. **Vector search capability**: the MCP server can implement semantic search if it has access to the `granite-embedding-30m-english` model for query embedding.
5. **Missing content types**: solutions and articles are not in the RAG core. The MCP server may need to fall back to the `portal` core (also available on this container) for those document types.

### What Stays the Same

1. **`portal` core is also available**: identical schema to the old OKP container, accessible at `/solr/portal/` on the same instance. The existing MCP server code works unchanged against this core.
2. **eDisMax search**: the `/hybrid-search` handler uses the same eDisMax pattern (just different field names and boost weights).
3. **Product synonym expansion**: still available via `product_txt` / `text_product_synonym`.
4. **Filter queries**: `fq` works the same way for narrowing results.

### Dual-Core Strategy

The RAG container supports a migration path:
- Use `portal` core for backward compatibility (solutions, articles, structured errata/CVE metadata)
- Use `portal-rag` core for semantic search on documentation and errata content
- Gradually shift search traffic as the RAG chunking pipeline covers more content types

## Open Questions

### Chunk Boundary Quality

The chunking pipeline produces many very short chunks (30% under 50 tokens). Some chunk_index=0 entries contain only the page title. How much do these near-empty chunks pollute KNN results? Would filtering `fq=num_tokens:[50 TO *]` improve semantic search quality?

### Missing Content Types

Solutions (157K in old core) and articles (7K) are absent from the RAG core. Is this a phased rollout, or are these document types intentionally excluded from the RAG pipeline?

### `documentKind` Taxonomy

The conflation of CVEs, documentation, and solutions into `documentKind:"unknown"` makes type-based filtering unreliable. The `source_path` prefix is a workaround, but a proper `documentKind` value would be cleaner. Is this planned for future data loads?

### Errata Metadata Loss

The old core had structured fields for errata (advisory type, severity, product names, product filter). The RAG core only has chunk text. For the MCP server's errata-specific search tools, should it query the `portal` core for metadata and the `portal-rag` core for content search?

### True Hybrid Search

The `/hybrid-search` handler name is slightly misleading - it's boosted lexical search, not lexical+vector fusion. True hybrid search (combining BM25 scores with KNN similarity) would need application-level score fusion. Is the team planning Solr-native hybrid via `{!bool}` or reranking, or should the MCP server handle this?

### Heading Navigation

The `headings` field provides section context (e.g., `"Chapter 18. Securing virtual machines,18.6. SELinux booleans for virtualization"`), but `heading_id_pairs` is unpopulated. Is this field intended to carry HTML anchor IDs for deep-linking into specific sections?

### Recency Boost Effectiveness

The `/hybrid-search` handler uses `recip(ms(NOW,lastModifiedDate),...)` for recency boosting, but all documents have `lastModifiedDate` set to the indexing timestamp (2026-03-09), not the original document's modification date. This makes the recency boost a no-op. Is this intentional, or should documents carry their real modification dates?
