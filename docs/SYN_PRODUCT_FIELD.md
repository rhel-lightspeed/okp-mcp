# syn_product Field Investigation

Investigation of the `syn_product` Solr field for product name synonym expansion (issue #144, item 1).

## Field Infrastructure

The Solr 9.10.1 server has a complete synonym expansion pipeline already configured:

| Component | Configuration |
|-----------|---------------|
| Field | `syn_product` - type `text_product_synonym`, indexed, **not stored** |
| Copy field | `product` -> `syn_product` (automatic) |
| Index analyzer | WhitespaceTokenizer -> LowerCase -> StopFilter -> SynonymGraphFilter -> RemoveDuplicates -> FlattenGraph |
| Query analyzer | WhitespaceTokenizer -> LowerCase -> StopFilter -> SynonymGraphFilter -> RemoveDuplicates |
| Synonym file | `common-product-name-synonyms.txt`, bidirectional (`expand=true`, `ignoreCase=true`) |

Both the index and query analyzers apply synonym expansion, so synonyms work regardless of which form was indexed or queried.

## Synonym File Contents

The file contains ~90 synonym rules covering:

- **RHEL**: `red hat enterprise linux,rhel` plus every point release from 2.1 through 9.4, with variant spellings (`rhel8`, `rhel-8`, `rhel_8`, `rhel 8`, `rhel8.0`, etc.)
- **RHEV/RHV**: `rhev, rhv, red hat enterprise virtualization, red hat virtualization` plus versions 2.x through 4.x
- **Satellite**: `rhsat,satellite`
- **JBoss**: `jboss-as,jboss as,jboss_as,jbossas`
- **RHEV subsystems**: `rhev-h`/`rhevh`, `rhev-m`/`rhevm`/`rhev-manager`

Notable gaps: no synonyms for OpenShift (OCP), Ansible (AAP), Ceph, Quay, or other products that commonly use abbreviations.

## Coverage Analysis

The `product` field (source for `syn_product`) is only populated on **documentation** pages:

| documentKind | Has `product`? | Count | % of corpus |
|--------------|:-:|------:|------:|
| documentation | yes | 16,946 | 2.8% |
| CVE | no | 320,349 | 53.2% |
| solution | no | 158,338 | 26.3% |
| Errata | no | 94,932 | 15.8% |
| article | no | 7,426 | 1.2% |
| other | no | 3,917 | 0.7% |
| **Total** | | **601,908** | |

Solutions and CVEs have no `product` field at all. Their stored fields are limited to `id`, `title`, `main_content`, `documentKind`, `lastModifiedDate`, `url_slug`, and `resourceName`.

This means `syn_product` is populated for 16,946 documents (2.8% of the corpus). The field contains 197 distinct terms across 143 unique product values.

## Verified Synonym Expansion

Direct queries confirm synonyms work correctly:

| Query | Field | numFound | Matched product |
|-------|-------|------:|-----------------|
| `syn_product:rhel` | syn_product | 1,959 | Red Hat Enterprise Linux (+ SAP, AI, Real Time, Atomic Host variants) |
| `syn_product:rhsat` | syn_product | 367 | Red Hat Satellite |
| `syn_product:rhev` | syn_product | 156 | Red Hat Virtualization |
| `syn_product:satellite` | syn_product | 367 | Red Hat Satellite |

The `rhsat` -> `satellite` and `rhel` -> `red hat enterprise linux` mappings both expand correctly in both directions.

## A/B Comparison Results

Tested with the current `qf` vs. adding `syn_product^6`:

### "satellite installation" (solution-heavy query)

Top 5 results **identical** with and without `syn_product^6`. All top hits are solutions (no `product` field), so `syn_product` contributes nothing to their scores.

### "RHEL 9 networking" (mixed query)

Same document ordering. Absolute scores dropped slightly with `syn_product^6` due to IDF redistribution from adding a sparse field, but rank order was preserved.

### "rhsat" (abbreviation-only query, via edismax)

`syn_product^6` correctly boosted Red Hat Satellite documentation pages. Without it, `rhsat` only matches via `all_content^1` (if present in body text).

## Impact Assessment

Adding `syn_product^6` to `qf` provides:

**Benefits:**
- Documentation pages for abbreviated product names (rhel, rhsat, rhev/rhv) get a strong relevance boost
- Zero risk: the field is already indexed and populated, this just starts querying it
- Helps queries where the user uses an abbreviation but the document title/content uses the full product name (or vice versa)

**Limitations:**
- Only affects 2.8% of the corpus (documentation pages)
- Solutions, CVEs, errata, and articles are unaffected (no `product` field)
- For queries where solutions dominate the top results, adding `syn_product` won't change the ranking
- The synonym file has gaps: no coverage for OCP, AAP, Ceph, Quay, and other commonly abbreviated products

## Changes Made

Two files changed, one constant added to each `qf` string:

- `src/okp_mcp/solr.py`: added `syn_product^6` to the base edismax `qf` assignment in `_solr_query()`
- `src/okp_mcp/portal.py`: added `syn_product^6` to `_MAIN_QF` (overrides the base `qf` on the main search path)

The `^6` weight puts product synonym matches above `title^5`, reflecting that a product name match via synonyms is a strong relevance signal for documentation pages.

## Future Considerations

- **Expand synonym file**: Add mappings for OCP/OpenShift, AAP/Ansible, and other commonly abbreviated products
- **Product field on non-documentation docs**: If the Solr schema were updated to populate `product` on solutions, CVEs, and errata, `syn_product` would become dramatically more useful
- **Weight tuning**: The `^6` weight may need adjustment based on functional test results; it should boost product-relevant docs without drowning out high-quality solutions that match on content
