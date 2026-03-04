# Solr Database Research: Red Hat Customer Portal Knowledge Base

Research conducted against `http://localhost:8983/solr/`, Solr instance running in a local container.

## Instance Overview

| Property | Value |
|----------|-------|
| Core name | `portal` |
| Schema | `mimir-config` v1.6 |
| Unique key | `resourceName` |
| Total documents | 583,786 |
| Index size | 2.97 GB |
| Segments | 1 |
| Last modified | 2026-02-01 |

Single core, read-only snapshot of the Red Hat Customer Portal content. Contains CVEs, errata/advisories, knowledge base solutions, product documentation, articles, and product catalog entries.

## Document Types (by `documentKind`)

| documentKind | Count | % of Total | Description |
|---|---|---|---|
| `Cve` | 313,587 | 53.7% | CVE vulnerability entries |
| `solution` | 156,557 | 26.8% | Knowledge base solutions (support cases) |
| `Errata` | 86,780 | 14.9% | Security/bug fix/enhancement advisories (RHSA, RHBA, RHEA) |
| `documentation` | 15,760 | 2.7% | Product documentation pages |
| `article` | 7,350 | 1.3% | Knowledge base articles |
| `PortalProduct` | 3,589 | 0.6% | Product catalog entries (download matrix) |
| `access-drupal10-node-type-page` | 150 | <0.1% | Portal CMS pages (policies, programs) |
| `access-drupal-node-type-page` | 9 | <0.1% | Legacy CMS pages |
| `rhdc-drupal-node-type-blog-post` | 2 | <0.1% | Blog posts |
| `access-drupal10-node-type-contact-page` | 1 | <0.1% | Contact page |
| `page` | 1 | <0.1% | Generic page |

The top 5 types (Cve, solution, Errata, documentation, article) account for 99.4% of all documents.

## Schema: Fields

### Core Identity Fields

| Field | Type | Stored | Indexed | Multi | Used In (doc count) | Notes |
|---|---|---|---|---|---|---|
| `resourceName` | string | yes | yes | no | 583,786 | **Unique key.** Path-based identifier. |
| `id` | string | yes | yes | no | 583,786 | Required. Errata use advisory ID (e.g. `RHSA-2022:4915`), others use path. |
| `doc_id` | string | yes | yes | no | 583,786 | Required. UUID for each document. |
| `documentKind` | string | yes | yes | no | 583,786 | Document type discriminator. |

### Content Fields

| Field | Type | Stored | Indexed | Multi | Used In (doc count) | Notes |
|---|---|---|---|---|---|---|
| `title` | text_general | yes | yes | no | 309,381 | Page title. Copied to `all_content`. |
| `allTitle` | text_general | yes | yes | no | 400,367 | Combined/display title. |
| `content` | text_general | yes | yes | no | 309,408 | Short content/breadcrumb text. Copied to `all_content`. |
| `main_content` | text_general | yes | yes | no | 580,197 | Primary body text. Copied to `all_content`. |
| `heading_h1` | text_general | yes | yes | yes | 60,168 | H1 headings extracted from HTML. Copied to `all_content`. |
| `heading_h2` | text_general | yes | yes | yes | 302,590 | H2 headings extracted from HTML. Copied to `all_content`. |
| `all_content` | text_en_splitting_tight | **no** | yes | yes | 580,197 | **Search-only aggregate.** Not stored, not retrievable. Copy target from: content, documentation_version, heading_h1, heading_h2, main_content, product, title. |
| `syn_product` | text_product_synonym | **no** | yes | no | 15,760 | **Search-only.** Copy of `product` with synonym expansion. Not stored. |

### URL/Navigation Fields

| Field | Type | Stored | Indexed | Multi | Used In (doc count) | Notes |
|---|---|---|---|---|---|---|
| `url_slug` | string | yes | yes | no | 477,494 | Short identifier (CVE ID, solution number, etc.) |
| `view_uri` | string | yes | yes | no | 400,367 | Display/canonical URI path. |
| `uri` | string | yes | yes | no | 3,589 | Used by PortalProduct only. Download URI key. |
| `pdf_path` | string | yes | yes | no | ~0 | PDF download path (rarely populated). |

### Date Fields

| Field | Type | Stored | Indexed | Default | Used In (doc count) | Notes |
|---|---|---|---|---|---|---|
| `lastModifiedDate` | pdate | yes | yes | `NOW` | 583,786 (all) | Last modification timestamp. |
| `timestamp` | pdate | yes | yes | `NOW` | 583,786 (all) | Indexing timestamp. |
| `portal_publication_date` | pdate | yes | yes | - | ~86,780 | Errata publication date. |
| `cve_publicDate` | pdate | yes | yes | - | ~40,592 | CVE public disclosure date. |

### CVE-Specific Fields

| Field | Type | Stored | Used In | Notes |
|---|---|---|---|---|
| `cve_details` | string | yes | 313,587 | CVE description text. |
| `cve_threatSeverity` | string | yes | 40,592 | Severity: Critical (2,501), Important (6,059), Moderate (21,706), Low (10,326). **Not populated for ~273K CVEs** (those that don't affect Red Hat software). |
| `cve_publicDate` | pdate | yes | ~40,592 | Public disclosure date. |

### Errata-Specific Fields

| Field | Type | Stored | Used In | Notes |
|---|---|---|---|---|
| `portal_advisory_type` | string | yes | 86,780 | Bug Fix Advisory (55,653), Security Advisory (21,422), Product Enhancement Advisory (9,705). |
| `portal_severity` | string | yes | 79,283 | None (53,886), Important (11,494), Moderate (7,557), (none) (3,975), Critical (1,238), Low (1,133). |
| `portal_synopsis` | text_general | yes | 86,780 | Short advisory summary. |
| `portal_summary` | text_general | yes | 86,780 | Longer advisory description. |
| `portal_product_names` | string (multi) | yes | 86,779 | Affected product names. |
| `portal_product_filter` | string (multi) | yes | 86,780 | Structured filter: `Product|Variant|Version|Arch`. |

### Documentation-Specific Fields

| Field | Type | Stored | Used In | Notes |
|---|---|---|---|---|
| `product` | string | yes | 15,760 | Product name. Copied to `all_content` and `syn_product`. |
| `documentation_version` | string | yes | 15,760 | Doc version string. Copied to `all_content`. |
| `portal_content_subtype` | string | yes | 15,760 | Always `"title"` for documentation. |

### PortalProduct-Specific Fields

| Field | Type | Stored | Used In | Notes |
|---|---|---|---|---|
| `portal_product` | string | yes | 3,586 | Product name. |
| `portal_product_variant` | string | yes | 3,586 | Product variant. |
| `portal_product_version` | string | yes | 3,589 | Product version. |
| `portal_product_platform` | string | yes | 3,589 | Platform (e.g., `RHEL 8`, `RHEL 9`). |
| `portal_architecture` | string | yes | 3,586 | Architecture (x86_64, aarch64, s390x, ppc64le). |
| `portal_product_minor` | string | yes | ~0 | Minor version (rarely used). |

### Metadata Fields

| Field | Type | Stored | Used In | Notes |
|---|---|---|---|---|
| `stream_content_type` | string | yes | 580,197 | Always `text/html`. |
| `details_source` | string | yes | 0 | Defined but never populated. |

## Schema: Field Types

| Type Name | Solr Class | Key Characteristics |
|---|---|---|
| `string` | StrField | Exact match, sortable, doc values |
| `text_general` | TextField | Standard tokenizer + stopwords + lowercase |
| `text_en` | TextField | English: standard tokenizer + stopwords + lowercase + possessive + Porter stemming. Synonyms on query side. |
| `text_en_splitting` | TextField | English with word splitting: whitespace tokenizer + word delimiter + stemming |
| `text_en_splitting_tight` | TextField | English with minimal stemming: standard tokenizer + stopwords + EnglishMinimalStem + dedup |
| `text_product_synonym` | TextField | Product name synonyms: whitespace tokenizer + synonym expansion from `common-product-name-synonyms.txt` |
| `text_ws` | TextField | Whitespace only, no analysis |
| `pdate` | DatePointField | Point-based date with doc values |
| `plong` | LongPointField | Point-based long with doc values |

## Schema: Copy Fields

These define the search aggregation strategy:

```
content            → all_content
documentation_version → all_content
heading_h1         → all_content
heading_h2         → all_content
main_content       → all_content
product            → all_content
title              → all_content
product            → syn_product
```

`all_content` is the unified search field (not stored, index-only). It aggregates all textual content with `text_en_splitting_tight` analysis (minimal stemming, dedup).

`syn_product` enables product name synonym expansion (e.g., "RHEL" matches "Red Hat Enterprise Linux").

## Schema: Dynamic Fields

| Pattern | Type | Notes |
|---|---|---|
| `*_txt_en_split_tight` | text_en_splitting_tight | English tight splitting |
| `*_txt_en_split` | text_en_splitting | English word splitting |
| `*_txt_sort` | text_gen_sort | Sortable text (multi) |
| `*_t_sort` | text_gen_sort | Sortable text (single) |
| `*_txt_en` | text_en | English full analysis |
| `*_ws` | text_ws | Whitespace-only |
| `ignored_*` | ignored | Discarded during indexing |

## Request Handlers

### `/select` (Primary Search)

The main search endpoint. Pre-configured with eDisMax.

**Query fields (qf) with boosts:**
| Field | Boost | Purpose |
|---|---|---|
| `url_slug` | ^20 | Exact ID/slug matches rank highest |
| `title` | ^15 | Title matches |
| `main_content` | ^10 | Body content |
| `product` | ^8 | Product name |
| `syn_product` | ^6 | Product synonyms |
| `documentation_version` | ^5 | Version strings |
| `all_content` | ^2 | Catch-all aggregate |

**Phrase boosting:**
- `pf`: title^20, main_content^15
- `pf2`: title^12, main_content^6 (bigrams)
- `pf3`: title^16, main_content^10 (trigrams)

**Minimum match (mm):** `3<-1 5<70% 9<60%`
- 1-3 terms: all required
- 4-5 terms: all minus 1
- 5-8 terms: 70% required
- 9+ terms: 60% required

**Default field list (fl):** id, url_slug, title, main_content, product, resourceName, documentation_version, pdf_path, documentKind, score, lastModifiedDate, portal_product_names, view_uri, portal_advisory_type, allTitle, portal_summary, portal_synopsis, portal_severity, portal_publication_date, uri, portal_product, portal_product_variant, portal_product_version, portal_product_platform, portal_architecture, portal_product_minor, portal_product_filter, cve_publicDate, cve_threatSeverity, cve_details

**Facets enabled by default on:** documentKind, portal_content_subtype, product, documentation_version

**Highlighting enabled** on: id, title, main_content, product, resourceName, documentation_version (150 char fragments, HTML-encoded, `<b>` tags)

### `/select-errata` (Errata-Specific Search)

Specialized handler for errata/advisory queries.

**Query fields (qf):** id^15, title^15, portal_summary^12, content^12, main_content^10

**Field list (fl):** id, title, main_content, resourceName, documentKind, score, portal_product_names, portal_advisory_type, portal_summary, portal_synopsis, portal_severity, portal_publication_date, view_uri, portal_product_filter

### `/browse` (Velocity UI)

Velocity-templated UI search. Same field weights as `/select` with added More Like This (MLT) config:
- MLT fields: product, main_content
- MLT qf: product^10, main_content^10
- MLT count: 3

Also includes facet pivots: `product,documentation_version` and `documentKind,portal_content_subtype`.

## Document Type Details and Sample Shapes

### CVE Documents (313,587 docs)

Two populations: ~40K with severity data (affects Red Hat), ~273K without (doesn't affect Red Hat).

```json
{
  "id": "/security/cve/CVE-2024-9823/index.html",
  "documentKind": "Cve",
  "url_slug": "CVE-2024-9823",
  "allTitle": "CVE-2024-9823",
  "view_uri": "/security/cve/CVE-2024-9823/",
  "cve_details": "A flaw was found in Jetty...",
  "cve_threatSeverity": "Moderate",
  "cve_publicDate": "2024-10-14T15:03:02Z",
  "main_content": "CVE-2024-9823 Public on...",
  "lastModifiedDate": "2025-10-06T08:54:35Z"
}
```

**Severity distribution** (of 40,592 CVEs with severity):
- Moderate: 21,706 (53.5%)
- Low: 10,326 (25.4%)
- Important: 6,059 (14.9%)
- Critical: 2,501 (6.2%)

### Errata/Advisory Documents (86,780 docs)

```json
{
  "id": "RHSA-2022:4915",
  "documentKind": "Errata",
  "title": "RHSA-2022:4915 - Important: rh-postgresql12-postgresql security update...",
  "view_uri": "/errata/RHSA-2022:4915/",
  "portal_advisory_type": "Security Advisory",
  "portal_severity": "Important",
  "portal_synopsis": "Important: rh-postgresql12-postgresql security update",
  "portal_summary": "An update for rh-postgresql12-postgresql is now available...",
  "portal_product_names": ["Red Hat Software Collections (for RHEL Server)", ...],
  "portal_product_filter": ["Red Hat Software Collections|...|1|x86_64", ...],
  "portal_publication_date": "2022-06-06T06:50:06Z",
  "main_content": "Issued: 2022-06-06 Updated: 2022-06-06 RHSA-2022:4915...",
  "heading_h2": ["synopsis", "type-severity", "topic", "description", "solution", ...]
}
```

**Advisory type distribution:**
- Bug Fix Advisory: 55,653 (64.1%)
- Security Advisory: 21,422 (24.7%)
- Product Enhancement Advisory: 9,705 (11.2%)

**Severity distribution:**
- None: 53,886 (67.9%)
- Important: 11,494 (14.5%)
- Moderate: 7,557 (9.5%)
- (none): 3,975 (5.0%)
- Critical: 1,238 (1.6%)
- Low: 1,133 (1.4%)

### Solution Documents (156,557 docs)

```json
{
  "id": "/solutions/3257611/index.html",
  "documentKind": "solution",
  "url_slug": "3257611",
  "title": "usage of the service.alpha.kubernetes.io/tolerate-unready-endpoints annotation in OpenShift",
  "main_content": "...Solution Unverified - Updated 14 Jun 2024 Environment OpenShift Container Platform 3.6 Issue...Resolution...",
  "heading_h2": ["environment", "issue", "resolution"],
  "lastModifiedDate": "2024-06-14T17:18:29Z"
}
```

Solutions follow a structured format: Environment, Issue, Resolution (sometimes with Diagnostic Steps).

### Documentation Documents (15,760 docs)

```json
{
  "id": "/documentation/en-us/openshift_sandboxed_containers/1.10/html-single/deploying.../index.html",
  "documentKind": "documentation",
  "product": "OpenShift sandboxed containers",
  "documentation_version": "1.10",
  "portal_content_subtype": "title",
  "title": "Deploying Red Hat build of Trustee - OpenShift sandboxed containers 1.10",
  "main_content": "...",
  "heading_h1": ["title", "legalnotice"],
  "heading_h2": ["subtitle", "providing-feedback-on-red-hat-documentation", "title"]
}
```

**Top products** (by doc count): OpenShift Container Platform (1,970), RHEL (1,871), OpenStack Platform (1,153), Red Hat Integration (716), JBoss Operations Network (635).

**Top versions:** 5 (878), 6 (601), 8 (393), 3.2 (379), 3.1 (378), 4.8 (269).

### Article Documents (7,350 docs)

```json
{
  "id": "/articles/2585/index.html",
  "documentKind": "article",
  "url_slug": "2585",
  "title": "How do I debug problems in my startup scripts?",
  "main_content": "How do I debug problems in my startup scripts? Updated 16 Sept 2012..."
}
```

Simpler structure than solutions. No structured sections, just title + body content.

### PortalProduct Documents (3,589 docs)

```json
{
  "id": "8bf6b478-9ee5-4a7c-a1e7-cbe87a19cd4b",
  "documentKind": "PortalProduct",
  "uri": "downloads_860|rhel|8|1.2|aarch64",
  "portal_product": "Red Hat OpenShift Builds",
  "portal_product_variant": "Red Hat OpenShift Builds for ARM",
  "portal_product_version": "1.2",
  "portal_product_platform": "RHEL 8",
  "portal_architecture": "aarch64"
}
```

Product catalog entries. No textual content, purely structured metadata for the download matrix. The `uri` field encodes a pipe-delimited key: `downloadId|platform|majorVersion|productVersion|arch`.

## Update Processing Chains

### `extract` chain
Used by `/update/extract` (Tika-based HTML ingestion). Performs regex replacements, field cloning, UUID generation, deduplication, and blank removal.

### `errata-portal-product` chain
Used by `/update/json/docs` for JSON-based ingestion of errata and product catalog data. Generates UUIDs for `doc_id` and clones fields.

## Query Elevation

The instance has a `QueryElevationComponent` configured, reading from `elevate.xml`. This allows manually boosting or excluding specific documents for specific queries.

## Key Observations for MCP Server Design

### Field Population Varies by Document Type

Not all fields are populated for all document types. The MCP server should be aware of which fields are meaningful for which `documentKind`:

| Field Group | Populated For |
|---|---|
| Core (id, doc_id, documentKind, resourceName, lastModifiedDate) | All types |
| Content (title, main_content, content) | All except PortalProduct |
| URL (url_slug, view_uri) | Cve, Errata, solution, article |
| CVE (cve_details, cve_threatSeverity, cve_publicDate) | Cve only |
| Advisory (portal_advisory_type, portal_severity, portal_synopsis, portal_summary) | Errata only |
| Product metadata (portal_product_names, portal_product_filter) | Errata only |
| Documentation (product, documentation_version, portal_content_subtype) | documentation only |
| Product catalog (portal_product, portal_product_variant, portal_product_version, portal_product_platform, portal_architecture, uri) | PortalProduct only |

### Search Strategy

The `/select` handler is pre-configured with sensible eDisMax defaults. The MCP server can either:
1. Pass queries directly and rely on the handler defaults (simplest)
2. Override specific parameters (e.g., `fq=documentKind:Cve` to scope to CVEs)
3. Use `/select-errata` for errata-specific searches

### Filtering Patterns

Useful filter query (`fq`) patterns:
- By type: `fq=documentKind:Cve`
- By severity: `fq=cve_threatSeverity:Critical` or `fq=portal_severity:Important`
- By product: `fq=product:"Red Hat Enterprise Linux"`
- By product (errata): `fq=portal_product_names:"Red Hat Enterprise Linux"`
- By advisory type: `fq=portal_advisory_type:"Security Advisory"`
- By date range: `fq=lastModifiedDate:[2024-01-01T00:00:00Z TO *]`
- By version: `fq=documentation_version:9`

### Response Optimization

- `all_content` and `syn_product` are not stored, so they cannot be returned in results (search-only)
- `main_content` can be very large (full page body text), consider truncation or using highlighting snippets instead
- The default `fl` already includes the most useful fields
- Highlighting is pre-configured and returns 150-char snippets with `<b>` tags

## Open Questions

Areas that could inform MCP server design with further investigation.

### Temporal Distribution

Date fields exist but the actual date ranges haven't been analyzed. When does CVE coverage start? How fresh are the errata? Knowing the data's freshness profile matters for whether the MCP server should expose date-range filtering as a first-class feature.

### Product Synonym Mappings

`common-product-name-synonyms.txt` powers product name expansion (so "RHEL" matches "Red Hat Enterprise Linux"), but the actual mappings haven't been extracted. Those synonyms directly affect what shorthand users can use in queries, which is relevant for MCP tool parameter documentation and prompt design.

### URL Construction

`view_uri` and `resourceName` are captured but there's no documented pattern for constructing full customer portal URLs (presumably `https://access.redhat.com` + `view_uri`). The MCP server will need to return clickable links.

### `portal_product_filter` Decomposition

The pipe-delimited format (`Product|Variant|Version|Arch`) is documented but the distinct values haven't been extracted. That's the actual product/version/arch matrix available for errata filtering, which could inform whether the MCP server offers structured product filtering.

### CVE "Does Not Affect" Noise

~273K CVEs just say "CVE-XXXX does not affect Red Hat software" while ~40K have real severity/detail data. Worth determining whether the MCP server should filter out "does not affect" CVEs by default (e.g., `fq=cve_threatSeverity:*`) or return them.

### Query Performance

`QTime` values in responses ranged from 3-35ms during exploration, but response times under different query patterns, result sizes, and concurrent load haven't been systematically tested.
