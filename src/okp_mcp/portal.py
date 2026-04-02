"""Unified portal search: query builders, chunk conversion, RRF, and EOL product filtering."""

from __future__ import annotations

import asyncio
import html as html_mod
import re
from dataclasses import dataclass, field, replace

import httpx

from .config import logger
from .content import _select_within_budget, doc_uri, strip_boilerplate
from .formatting import _annotate_result
from .intent import apply_deprecation_boosts, apply_main_boosts
from .solr import _clean_query, _filter_rhv_sentences, _solr_query


@dataclass
class PortalChunk:
    """A single passage-level chunk extracted from a portal Solr document.

    Each highlight snippet or fallback text block becomes one PortalChunk.
    Multiple chunks can share the same ``parent_id`` when a document produces
    several highlight snippets.
    """

    doc_id: str
    parent_id: str | None = None
    title: str = ""
    chunk: str = ""
    chunk_index: int = 0
    num_tokens: int = 0
    online_source_url: str = ""
    documentKind: str = ""
    score: float | None = None
    rrf_score: float | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Facet extraction
# ---------------------------------------------------------------------------


def _extract_facet_counts(solr_response: dict) -> dict[str, dict[str, int]]:
    """Extract facet field counts from a Solr response.

    Solr returns ``facet.field`` data as alternating ``[value, count, value,
    count, ...]`` lists.  This converts them to ``{field: {value: count}}``
    dicts, dropping zero-count entries so downstream code only sees facets
    with actual results.

    Returns an empty dict when the response has no ``facet_counts`` key
    (e.g. in unit tests with minimal mock responses).
    """
    facet_fields = solr_response.get("facet_counts", {}).get("facet_fields", {})
    result: dict[str, dict[str, int]] = {}
    for field_name, pairs in facet_fields.items():
        counts: dict[str, int] = {}
        # Solr facet lists are always even-length ([value, count, ...]),
        # but guard against a malformed trailing element to avoid IndexError.
        for i in range(0, len(pairs) - 1, 2):
            value, count = pairs[i], pairs[i + 1]
            if count > 0:
                counts[value] = count
        if counts:
            result[field_name] = counts
    return result


_EOL_PRODUCTS: frozenset[str] = frozenset(
    [
        "Red Hat Virtualization",
        "Red Hat Hyperconverged Infrastructure for Virtualization",
        "Red Hat JBoss Operations Network",
        "Red Hat Fuse",
        "Red Hat Single Sign-On",
        "Red Hat Single Sign-On Continuous Delivery",
        "Red Hat CodeReady Workspaces",
        "Red Hat CodeReady Studio",
        "Red Hat JBoss Data Virtualization",
        "Red Hat Container Development Kit",
        "Red Hat Gluster Storage",
        "Red Hat JBoss Developer Studio",
        "Red Hat JBoss Developer Studio Integration Stack",
        "Red Hat Application Migration Toolkit",
        "Red Hat Software Collections",
        "JBoss Enterprise SOA Platform",
        "JBoss Enterprise Application Platform Continuous Delivery",
        "Red Hat Development Suite",
        "Red Hat Developer Toolset",
        "OpenShift Online",
        "Red Hat JBoss Fuse Service Works",
        "Red Hat Certificate System",
        "Red Hat Process Automation Manager",
        "Red Hat Decision Manager",
        "Red Hat OpenShift Container Storage",
    ]
)


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

# Fields returned by the main (all-type) query.  Includes type-specific fields
# (cve_details, portal_synopsis, ...) so the chunk-conversion layer (Phase 2)
# can fall back to them when highlighting returns no snippets.
_MAIN_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,"
    "product,documentation_version,lastModifiedDate,score,"
    "main_content,"
    "cve_details,cve_threatSeverity,"
    "portal_synopsis,portal_summary,portal_severity,portal_advisory_type"
)

# Fields returned by the deprecation query (no CVE/errata-specific fields).
_DEPRECATION_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,"
    "product,documentation_version,lastModifiedDate,score,main_content"
)

# Custom qf that adds errata-specific field boosts on top of the base edismax
# weights defined in solr._solr_query().  cve_details is a Solr `string` field
# (not tokenized) so it cannot participate in edismax scoring; CVEs match via
# allTitle, main_content, and all_content instead.
_MAIN_QF = "title^5 main_content heading_h1^3 heading_h2 portal_synopsis^3 allTitle^3 content^2 all_content^1"


def _build_eol_filter() -> str:
    """Build a Solr fq clause that excludes all EOL products."""
    return " AND ".join(f'-product:"{p}"' for p in _EOL_PRODUCTS)


def _build_main_query(cleaned_query: str) -> dict:
    """Build Solr params for the unified main query (all document types).

    The returned dict is passed to ``_solr_query()`` which merges it into its
    base edismax params.  Keys here override the base defaults (e.g. ``qf``,
    ``hl.defaultSummary``).

    Intent-specific boosts are applied by ``apply_main_boosts`` (from
    ``intent.py``) after construction.  See ``INTENT_RULES`` for the full
    list of detected intents and their boost parameters.
    """
    return {
        "q": cleaned_query,
        "fq": _build_eol_filter(),
        "qf": _MAIN_QF,
        "fl": _MAIN_FL,
        # Token budget control: _deduplicate_by_parent keeps only the best
        # chunk per parent doc, and max_results (default 10) caps the final
        # output.  Fetching 10 rows x 3 snippets = 30 intermediate chunks is
        # plenty of headroom.  Raising rows past ~15 pulls in marginally
        # relevant docs that bloat the tool response without improving answer
        # quality (measured via functional tests).
        "rows": 10,
        # 3 snippets per doc gives BM25 scoring enough candidates to pick a
        # good passage while keeping intermediate chunk count manageable.
        # Higher values (e.g. 6) create chunks that _deduplicate_by_parent
        # immediately discards, wasting Solr highlighting work.
        "hl.snippets": "3",
        # NOTE: Do NOT add hl.fragsize here.  The base default (600) with
        # hl.fragsizeIsMinimum=true is critical for structured content like
        # compatibility matrix tables.  Reducing it causes Solr to truncate
        # table data before the key rows (e.g. the RHEL container compat
        # matrix loses its "Unsupported" entries with fragsize < 600).
        #
        # Enable defaultSummary so docs/solutions/articles always get at least
        # the first N chars of main_content when highlight query terms don't
        # match.  CVE/errata boilerplate is handled in _docs_to_chunks() which
        # bypasses highlights for those types and uses type-specific fields.
        "hl.defaultSummary": "true",
        # Slight recency boost so newer RHEL content ranks higher.
        "bf": "recip(ms(NOW,lastModifiedDate),3.16e-11,1,1)^0.3",
    }


def _build_deprecation_query(cleaned_query: str) -> dict:
    """Build Solr params for the deprecation-focused query.

    Appends "deprecated removed" to the user query and heavily boosts
    documents whose titles or content mention deprecation/removal.  Restricted
    to docs/solutions/articles (CVEs/errata don't carry deprecation notices).
    """
    eol_fq = _build_eol_filter()
    return {
        "q": f"{cleaned_query} deprecated removed",
        "fq": [
            "documentKind:(solution OR article OR documentation)",
            eol_fq,
        ],
        "fl": _DEPRECATION_FL,
        # The deprecation query is a secondary signal, not the primary search.
        # 3 rows is enough to surface deprecation/removal notices for the
        # topic without flooding the final results with tangentially related
        # deprecation content (e.g. JBoss EAP deprecation appearing in
        # container compatibility results).
        "rows": 3,
        # 2 snippets per doc suffices because we only need to detect
        # deprecation signals, not extract comprehensive content.
        "hl.snippets": "2",
        # Shorter fragments are fine here: the deprecation query only needs
        # enough text to identify deprecation/removal notices, not full
        # tables or detailed procedures.  Unlike the main query (which needs
        # fragsize >= 600 for structured content like compat matrix tables),
        # deprecation signals are typically in short sentences.
        "hl.fragsize": "400",
        "bq": (
            'allTitle:(deprecated OR removed OR "no longer" OR "end of life")^20 '
            'allTitle:("release notes" OR "considerations in adopting")^15 '
            'main_content:(deprecated OR removed OR "no longer available")^10'
        ),
    }


# ---------------------------------------------------------------------------
# Chunk conversion
# ---------------------------------------------------------------------------

# Maximum characters of fallback content when highlighting returns no snippets.
# 400 chars captures the lead paragraph of most solutions/articles without
# pulling in boilerplate footers (e.g. fast-track publication notices).
# Increase cautiously: every extra char here multiplies across all results
# that lack highlight matches, inflating the tool response token count.
_FALLBACK_MAX_CHARS = 400

_ACCESS_BASE_URL = "https://access.redhat.com"


def _resolve_title(doc: dict) -> str:
    """Pick the best available title for a Solr document.

    Prefers ``allTitle`` (populated for most doc types), then ``title``,
    then ``heading_h1`` (list field, takes first element), then falls back
    to the document ID.
    """
    if doc.get("allTitle"):
        return doc["allTitle"]
    if doc.get("title"):
        return doc["title"]
    h1 = doc.get("heading_h1")
    if isinstance(h1, list) and h1:
        return h1[0]
    return doc.get("id", "Untitled")


def _build_doc_url(doc: dict) -> str:
    """Build a full access.redhat.com URL for a Solr document."""
    uri = doc_uri(doc)
    if not uri:
        return ""
    # doc_uri returns paths like /documentation/... or /security/cve/...
    if uri.startswith("http"):
        return uri
    return f"{_ACCESS_BASE_URL}{uri}"


def _fallback_cve(doc: dict) -> str:
    """Build fallback chunk text for a CVE without highlight snippets.

    Uses ``cve_details`` (the vulnerability description), prefixed with
    severity when available.
    """
    parts: list[str] = []
    severity = doc.get("cve_threatSeverity")
    if severity:
        parts.append(f"Severity: {severity}")
    details = doc.get("cve_details")
    if details:
        parts.append(details[:_FALLBACK_MAX_CHARS])
    return "\n".join(parts) if parts else ""


def _fallback_errata(doc: dict) -> str:
    """Build fallback chunk text for an erratum without highlight snippets.

    Uses ``portal_synopsis`` + ``portal_summary``, prefixed with advisory
    type and severity when available.
    """
    parts: list[str] = []
    advisory_type = doc.get("portal_advisory_type")
    severity = doc.get("portal_severity")
    if advisory_type or severity:
        meta = " | ".join(filter(None, [advisory_type, severity]))
        parts.append(meta)
    synopsis = doc.get("portal_synopsis")
    if synopsis:
        parts.append(synopsis)
    summary = doc.get("portal_summary")
    if summary:
        remaining = _FALLBACK_MAX_CHARS - sum(len(p) for p in parts)
        if remaining > 0:
            parts.append(summary[:remaining])
    return "\n".join(parts) if parts else ""


def _fallback_generic(doc: dict) -> str:
    """Build fallback chunk text for a doc/solution/article without highlights."""
    mc = doc.get("main_content", "")
    if mc:
        return strip_boilerplate(mc)[:_FALLBACK_MAX_CHARS]
    return ""


def _docs_to_chunks(
    solr_response: dict,
    query: str,
) -> list[PortalChunk]:
    """Convert a Solr response with highlighting into a flat list of PortalChunk chunks.

    CVE and Erratum documents always use type-specific fields (``cve_details``,
    ``portal_synopsis``) instead of highlights, since their ``main_content``
    starts with boilerplate.  All other document types use highlight snippets,
    falling back to ``_fallback_generic()`` when no snippets are available.

    RHV-contaminated sentences are filtered from highlight snippets via
    ``_filter_rhv_sentences()``.

    Args:
        solr_response: Full parsed Solr JSON response dict (must contain
            ``response.docs`` and ``highlighting``).
        query: Original user query (used for RHV filtering).

    Returns:
        List of PortalChunk chunks ordered by Solr rank, with multiple
        chunks per source document when highlighting produces multiple snippets.
    """
    docs = solr_response.get("response", {}).get("docs", [])
    highlighting = solr_response.get("highlighting", {})
    chunks: list[PortalChunk] = []

    for doc in docs:
        doc_id = doc.get("id", "")
        title = _resolve_title(doc)
        url = _build_doc_url(doc)
        kind = doc.get("documentKind", "")

        if kind in ("Cve", "Erratum"):
            chunk_text = _fallback_cve(doc) if kind == "Cve" else _fallback_errata(doc)
            if chunk_text:
                chunks.append(
                    PortalChunk(
                        doc_id=f"{doc_id}_fb_0",
                        parent_id=doc_id,
                        title=title,
                        chunk=chunk_text,
                        chunk_index=0,
                        num_tokens=len(chunk_text.split()),
                        online_source_url=url,
                        documentKind=kind,
                        score=doc.get("score"),
                    )
                )
            continue

        hl_snippets = highlighting.get(doc_id, {}).get("main_content", [])

        if hl_snippets:
            for i, snippet in enumerate(hl_snippets):
                chunk_text = re.sub(r"<[^>]+>", "", snippet).strip()
                # Decode HTML entities (&#x27; -> ', &#x2F; -> /, etc.)
                # before any further processing.  Solr highlights preserve
                # raw HTML entities from the indexed content, and leaving
                # them encoded wastes tokens and breaks regex patterns
                # (e.g. strip_boilerplate matching the apostrophe in
                # "Red Hat's fast-track publication program").
                chunk_text = html_mod.unescape(chunk_text)
                # Strip boilerplate from highlight snippets: Solr highlights
                # can include "This content is not included." markers and
                # fast-track publication footers that waste tokens without
                # adding useful info.  The fallback path (_fallback_generic)
                # already calls strip_boilerplate on main_content.
                chunk_text = strip_boilerplate(chunk_text)
                if query:
                    chunk_text = _filter_rhv_sentences(chunk_text, query)
                if not chunk_text:
                    continue
                chunks.append(
                    PortalChunk(
                        doc_id=f"{doc_id}_hl_{i}",
                        parent_id=doc_id,
                        title=title,
                        chunk=chunk_text,
                        chunk_index=i,
                        num_tokens=len(chunk_text.split()),
                        online_source_url=url,
                        documentKind=kind,
                        score=doc.get("score"),
                    )
                )
        else:
            chunk_text = _fallback_generic(doc)

            if chunk_text:
                chunks.append(
                    PortalChunk(
                        doc_id=f"{doc_id}_fb_0",
                        parent_id=doc_id,
                        title=title,
                        chunk=chunk_text,
                        chunk_index=0,
                        num_tokens=len(chunk_text.split()),
                        online_source_url=url,
                        documentKind=kind,
                        score=doc.get("score"),
                    )
                )

    return chunks


# ---------------------------------------------------------------------------
# Parent deduplication
# ---------------------------------------------------------------------------


def _deduplicate_by_parent(chunks: list[PortalChunk]) -> list[PortalChunk]:
    """Keep only the highest-ranked chunk per parent document.

    Multiple highlight snippets from the same source document share a
    ``parent_id`` but have unique ``doc_id`` values.  This collapses them
    to the single best passage (the one appearing earliest in the input,
    which preserves Solr/RRF rank order).

    Chunks without a ``parent_id`` are treated as unique and always kept.

    Args:
        chunks: PortalChunk list ordered by relevance (Solr rank or RRF score).

    Returns:
        Deduplicated list preserving original rank order.
    """
    if not chunks:
        return []

    seen_parents: set[str] = set()
    result: list[PortalChunk] = []

    for chunk in chunks:
        pid = chunk.parent_id
        if pid is None:
            # Orphan chunk, always keep
            result.append(chunk)
            continue
        if pid in seen_parents:
            continue
        seen_parents.add(pid)
        result.append(chunk)

    return result


# ---------------------------------------------------------------------------
# Reciprocal rank fusion (standalone, no rag dependency)
# ---------------------------------------------------------------------------


def _reciprocal_rank_fusion(
    *chunk_lists: list[PortalChunk],
    k: int = 60,
) -> list[PortalChunk]:
    """Merge chunk lists via reciprocal rank fusion, scored by cross-list consensus.

    For each unique ``doc_id``, sums ``1/(k + rank)`` across all lists where it
    appears (rank is 0-indexed). Chunks in multiple lists get higher scores.

    Args:
        *chunk_lists: Any number of PortalChunk lists to merge.
        k: RRF constant (default 60, per Cormack et al. 2009).

    Returns:
        Merged list sorted by descending RRF score, with ``rrf_score`` set.
    """
    if not chunk_lists:
        return []

    scores: dict[str, float] = {}
    selected: dict[str, PortalChunk] = {}

    for chunks in chunk_lists:
        for rank, chunk in enumerate(chunks):
            scores[chunk.doc_id] = scores.get(chunk.doc_id, 0.0) + 1.0 / (k + rank)
            # When the same chunk appears in multiple lists (e.g. main + deprecation),
            # keep the version with the longest text.  Different queries produce
            # different highlight snippets for the same document; the longer snippet
            # preserves more useful detail (commands, parameters) for the LLM.
            if chunk.doc_id not in selected or len(chunk.chunk) > len(selected[chunk.doc_id].chunk):
                selected[chunk.doc_id] = chunk

    return [
        replace(selected[doc_id], rrf_score=score)
        for doc_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]


# ---------------------------------------------------------------------------
# Score-based quality gate
# ---------------------------------------------------------------------------

# Minimum Solr score as a fraction of the top-scoring chunk.  Chunks below
# this threshold are dropped before the final top-N slice.  This eliminates
# tail results that matched only tangentially (e.g. "NIC compatibility" or
# "SSSD compatibility" appearing in a search for "container compatibility").
#
# Set conservatively at 0.45 (45% of top score).  In practice, genuinely
# relevant results cluster at 60-100% of top, while noise drops to 30-40%.
# The 45% threshold sits in the natural gap between those clusters.
#
# IMPORTANT: Solr scores from the main and deprecation queries use different
# query terms and boosts, so they are not perfectly comparable after RRF
# fusion.  A generous threshold avoids false-positive filtering caused by
# cross-query score differences.  Do not tighten below ~0.40 without
# testing across all functional cases.
_MIN_SCORE_RATIO = 0.45


def _filter_by_score(chunks: list[PortalChunk]) -> list[PortalChunk]:
    """Drop chunks whose Solr score falls below a fraction of the top score.

    This is a quality gate, not a ranking mechanism: it removes obvious noise
    without altering the relative order of surviving chunks.  Chunks without
    a score (None) are always kept.
    """
    if not chunks:
        return chunks

    top_score = max((c.score for c in chunks if c.score is not None), default=0.0)
    if top_score <= 0:
        return chunks

    threshold = top_score * _MIN_SCORE_RATIO
    kept = [c for c in chunks if c.score is None or c.score >= threshold]

    if len(kept) < len(chunks):
        logger.info(
            "Score filter: dropped %d/%d chunks below %.1f%% of top score (%.1f)",
            len(chunks) - len(kept),
            len(chunks),
            _MIN_SCORE_RATIO * 100,
            top_score,
        )

    return kept


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_DEPRECATION_WARNING = (
    "WARNING: Some results indicate a feature was deprecated or removed. If sources\n"
    "disagree, treat the deprecation/removal notice as authoritative over workarounds\n"
    "for other products.\n\n"
)


async def _run_portal_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_endpoint: str,
    # Reduced from 10 to 7 to match search_portal default.  7 is the
    # sweet spot: enough coverage for cross-referencing deprecation vs
    # current status, but avoids returning low-relevance tail results
    # that inflate the tool response without improving answer quality.
    max_results: int = 7,
) -> tuple[list[PortalChunk], bool, dict[str, dict[str, int]]]:
    """Execute the unified portal search pipeline.

    Cleans the query, fires main + deprecation queries in parallel, converts
    results to chunks, merges via RRF, deduplicates, and returns the top-N
    chunks plus a flag indicating whether deprecation content was found and
    facet counts from the main query.

    Args:
        query: Raw user query string.
        client: Shared httpx.AsyncClient (typed as object to avoid httpx import
            at module level; the actual type is enforced by ``_solr_query``).
        solr_endpoint: Full Solr endpoint URL for the portal core.
        max_results: Maximum chunks to return after deduplication.

    Returns:
        Tuple of (top-N PortalChunk list, has_deprecation bool, facet counts dict).
        Facet counts come from the main query only (the deprecation query is
        filtered to docs/solutions/articles, so its facets are not representative
        of the full corpus distribution).
    """
    cleaned = _clean_query(query)
    query_lower = query.lower()

    main_params = _build_main_query(cleaned)
    apply_main_boosts(main_params, query_lower, cleaned)

    dep_params = _build_deprecation_query(cleaned)
    # Apply intent-aware boosts to the deprecation query so it finds
    # deprecation notices about the user's ACTUAL topic, not random
    # deprecation content.  Without this, a VM management query gets
    # Eclipse Vert.x and network teaming deprecation results that waste
    # ~2,000 chars of response budget (see RSPEED_2480).
    apply_deprecation_boosts(dep_params, query_lower)

    main_data, dep_data = await asyncio.gather(
        _solr_query(main_params, client=client, solr_endpoint=solr_endpoint),
        _solr_query(dep_params, client=client, solr_endpoint=solr_endpoint),
    )

    main_chunks = _docs_to_chunks(main_data, query)
    dep_chunks = _docs_to_chunks(dep_data, query)

    merged = _reciprocal_rank_fusion(main_chunks, dep_chunks)
    deduped = _deduplicate_by_parent(merged)
    quality_filtered = _filter_by_score(deduped)

    top_n = quality_filtered[:max_results]

    dep_parent_ids = {d.parent_id for d in dep_chunks if d.parent_id is not None}
    has_deprecation = any(c.parent_id in dep_parent_ids for c in top_n if c.parent_id is not None)

    # Extract facet counts from the main query only.  The deprecation query
    # is filtered to docs/solutions/articles, so its facets would
    # misrepresent the full corpus distribution.
    facets = _extract_facet_counts(main_data)

    logger.info(
        "Portal search: query=%r main=%d dep=%d merged=%d deduped=%d returned=%d",
        query,
        len(main_chunks),
        len(dep_chunks),
        len(merged),
        len(deduped),
        len(top_n),
    )

    return top_n, has_deprecation, facets


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

# Cap content per search result to prevent a single large document from
# consuming the entire tool response.  Solr highlights with
# hl.fragsizeIsMinimum=true extend to sentence boundaries and can produce
# fragments of 2000+ characters.  Without this cap, a single large highlight
# can push other results out of the token budget entirely.
#
# 1500 chars is enough to include structured data like the RHEL container
# compatibility matrix table (~800 chars for the key rows) while still
# leaving room for ~8 other results in a typical 30K char budget.
# See also: formatting.py _MAX_RESULT_CONTENT (used by the legacy
# _format_result path, not this portal chunk path).
_MAX_CHUNK_CONTENT = 1500

_KIND_LABELS: dict[str, str] = {
    "documentation": "Documentation",
    "solution": "Solution",
    "article": "Article",
    "Cve": "CVE",
    "Erratum": "Security Advisory",
    "access-drupal10-node-type-page": "Documentation",
}

# Labels for facet summary output.  Mirrors _KIND_LABELS but uses shorter
# forms suitable for the compact "Result distribution:" line.
_KIND_FACET_LABELS: dict[str, str] = {
    "Cve": "CVE",
    "Erratum": "Advisory",
    "solution": "Solution",
    "documentation": "Documentation",
    "article": "Article",
    "access-drupal10-node-type-page": "Documentation",
}


def _format_facet_summary(facets: dict[str, dict[str, int]]) -> str:
    """Render ``documentKind`` facet counts as a compact one-liner for LLM context.

    Produces a string like ``"Result distribution: 487 CVE, 3 Solution, 1 Documentation"``
    sorted by descending count.  Returns an empty string when no ``documentKind``
    facet data is available.
    """
    kind_counts = facets.get("documentKind", {})
    if not kind_counts:
        return ""
    parts: list[str] = []
    for kind, count in sorted(kind_counts.items(), key=lambda x: x[1], reverse=True):
        label = _KIND_FACET_LABELS.get(kind, kind)
        parts.append(f"{count} {label}")
    return "Result distribution: " + ", ".join(parts)


def _format_portal_chunk(chunk: PortalChunk) -> tuple[str, int]:
    """Render a single PortalChunk as a markdown result block.

    Uses ``_annotate_result()`` from ``formatting.py`` to detect deprecation,
    replacement, and EOL signals. Returns ``(formatted_text, sort_key)``
    where sort_key controls ordering (replacement first, EOL last).
    """
    annotations, applicability, sort_key = _annotate_result(
        title=chunk.title,
        highlights=chunk.chunk,
        content=chunk.chunk,
    )

    lines: list[str] = []
    if annotations:
        lines.append(" | ".join(annotations))

    lines.append(f"**{chunk.title}**")

    kind_label = _KIND_LABELS.get(chunk.documentKind, chunk.documentKind)
    lines.append(f"Type: {kind_label} | Applicability: {applicability}")

    if chunk.online_source_url:
        lines.append(f"URL: {chunk.online_source_url}")

    if chunk.chunk:
        # Hard-truncate oversized chunks.  The LLM can always call
        # get_document for the full content if the snippet isn't enough.
        content = chunk.chunk
        if len(content) > _MAX_CHUNK_CONTENT:
            content = content[:_MAX_CHUNK_CONTENT] + " [...]"
        lines.append(f"Content: {content}")

    return "\n".join(lines), sort_key


def _format_portal_results(
    chunks: list[PortalChunk],
    has_deprecation: bool,
    query: str,
    max_response_chars: int,
    facets: dict[str, dict[str, int]] | None = None,
) -> str:
    """Format all chunks into a final string response with budget enforcement.

    Renders each chunk via ``_format_portal_chunk()``, sorts by annotation
    priority (replacements first, EOL last), prepends a deprecation warning
    banner if any chunk triggered deprecation detection, adds a facet
    distribution summary when available, and enforces the character budget
    via ``_select_within_budget()``.
    """
    if not chunks:
        return f"No results found for: {query}"

    formatted_pairs = [_format_portal_chunk(c) for c in chunks]
    formatted_pairs.sort(key=lambda x: x[1])
    sorted_texts = [text for text, _ in formatted_pairs]

    has_dep_annotation = has_deprecation or any(sk <= 0 for _, sk in formatted_pairs)

    output = _select_within_budget(sorted_texts, max_response_chars, query)

    if has_dep_annotation:
        output = _DEPRECATION_WARNING + output

    # Prepend facet distribution summary so the LLM can see the overall
    # result landscape (e.g. "mostly CVEs, few solutions").
    facet_line = _format_facet_summary(facets) if facets else ""
    if facet_line:
        output = facet_line + "\n\n" + output

    return output
