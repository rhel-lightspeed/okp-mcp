"""Unified portal search: query builders, intent detection, and EOL product filtering."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import httpx

from .config import logger
from .content import _select_within_budget, doc_uri, strip_boilerplate
from .formatting import _annotate_result
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

# Highlight term expansions injected into hl.q for intent-specific queries.
_VM_HIGHLIGHT_TERMS = "virsh cockpit deprecated virt-manager"
_EUS_HIGHLIGHT_TERMS = '"Enhanced EUS" "48 months" "Enhanced Extended Update Support"'
# SPICE highlight terms: VNC is the supported replacement for the deprecated
# SPICE display protocol.  Including "VNC" in hl.q causes Solr to select
# highlight snippets that mention VNC, which is critical for the LLM to
# recommend the correct replacement.  Without this, the VM intent's
# cockpit/virsh terms dominate snippets and the LLM omits VNC entirely.
# See functional test RSPEED_2481.
_SPICE_HIGHLIGHT_TERMS = "VNC deprecated removed replacement"


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


_VM_INTENT_RE = re.compile(r"\b(?:vm|vms|virtual machine|virtualization|hypervisor)\b")
_RELEASE_DATE_INTENT_RE = re.compile(r"\b(?:release dates?|released|when was|general availability)\b")
_EUS_INTENT_RE = re.compile(r"\b(?:eus|extended update support)\b")
# SPICE is a display/graphics protocol for VMs, NOT a VM management tool.
# Queries mentioning SPICE need display-protocol-specific boosts (VNC),
# not VM management boosts (cockpit/virsh).  This regex detects SPICE
# intent so _apply_intent_boosts can override the generic VM intent.
_SPICE_INTENT_RE = re.compile(r"\bspice\b")


def _detect_vm_intent(query_lower: str) -> bool:
    """Return True if the lowercased query contains VM/virtualization keywords."""
    return bool(_VM_INTENT_RE.search(query_lower))


def _detect_release_date_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about release dates or when something was released."""
    return bool(_RELEASE_DATE_INTENT_RE.search(query_lower))


def _detect_eus_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about EUS or Extended Update Support."""
    return bool(_EUS_INTENT_RE.search(query_lower))


def _detect_spice_intent(query_lower: str) -> bool:
    """Return True if the lowercased query mentions the SPICE display protocol.

    SPICE questions are about display protocol availability and replacements
    (VNC), not about VM management tools (cockpit/virsh).  Detecting SPICE
    intent separately from VM intent prevents the generic VM boosts from
    flooding results with irrelevant cockpit/virsh content.
    """
    return bool(_SPICE_INTENT_RE.search(query_lower))


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

    Intent-specific boosts (VM, EUS, release-date) are applied by
    ``_apply_intent_boosts`` after construction.
    """
    return {
        "q": cleaned_query,
        "fq": _build_eol_filter(),
        "qf": _MAIN_QF,
        "fl": _MAIN_FL,
        "rows": 20,
        "hl.snippets": "6",
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
        "rows": 5,
        "hl.snippets": "4",
        "bq": (
            'allTitle:(deprecated OR removed OR "no longer" OR "end of life")^20 '
            'allTitle:("release notes" OR "considerations in adopting")^15 '
            'main_content:(deprecated OR removed OR "no longer available")^10'
        ),
    }


def _apply_intent_boosts(params: dict, query_lower: str, cleaned_query: str) -> None:
    """Mutate *params* in-place to add intent-specific bq/hl.q boosts.

    Called after ``_build_main_query`` to layer on VM, EUS, release-date,
    or SPICE boosts without complicating the base query builder.

    Order matters: later intents overwrite earlier ones.  SPICE runs after
    VM so that "SPICE for VMs" gets display-protocol boosts (VNC), not
    VM-management boosts (cockpit/virsh).
    """
    if _detect_vm_intent(query_lower):
        params["bq"] = (
            'title:(cockpit OR virtualization OR "virt-manager")^15 '
            'main_content:(cockpit OR "cockpit-machines" OR virsh)^5'
        )
        params["hl.q"] = f"{cleaned_query} {_VM_HIGHLIGHT_TERMS}"

    # SPICE intent MUST run after VM intent so it overwrites the VM boosts.
    # SPICE questions are about the display protocol (SPICE vs VNC), not
    # about VM management tools (cockpit/virsh).  Without this override,
    # queries like "Is SPICE available for VMs?" trigger VM intent, which
    # injects cockpit/virsh highlight terms that flood results with
    # irrelevant VM management content and push out the critical VNC
    # replacement information the LLM needs to answer correctly.
    # See functional test RSPEED_2481.
    if _detect_spice_intent(query_lower):
        params["bq"] = (
            'allTitle:(spice OR deprecated OR "no longer")^15 '
            'main_content:(VNC OR deprecated OR removed OR replacement OR "no longer")^10'
        )
        params["hl.q"] = f"{cleaned_query} {_SPICE_HIGHLIGHT_TERMS}"

    if _detect_eus_intent(query_lower):
        params["bq"] = 'title:"Enhanced EUS"^100 title:"EUS FAQ"^80'
        params["hl.q"] = f"{cleaned_query} {_EUS_HIGHLIGHT_TERMS}"

    if _detect_release_date_intent(query_lower):
        params["bq"] = 'title:"Enterprise Linux Release Dates"^200 allTitle:"release dates"^30'


@dataclass(frozen=True, slots=True)
class _DeprecationIntentBoost:
    """Maps an intent detector to Solr boost terms for the deprecation query.

    Each entry pairs a detection function with the allTitle and main_content
    terms that should be boosted when that intent is active.  This ensures
    the deprecation query finds deprecation notices about the user's actual
    topic rather than unrelated deprecation content.

    Attributes:
        detect: Function that takes a lowercased query and returns True if
            this intent is active.
        title_terms: Solr OR-joined terms for the allTitle boost.
        content_terms: Solr OR-joined terms for the main_content boost.
    """

    detect: Callable[[str], bool]
    title_terms: str
    content_terms: str


# Registry of intent-specific boosts for the deprecation query.
#
# ORDER MATTERS: entries are evaluated first-match-wins.  SPICE must come
# before VM because SPICE queries contain VM terms ("VMs") but need
# display-protocol boosts (VNC), not VM management boosts (cockpit/virsh).
#
# To add a new intent: append a _DeprecationIntentBoost entry at the
# correct priority position and add a functional test to verify.
_DEPRECATION_INTENT_BOOSTS: list[_DeprecationIntentBoost] = [
    # SPICE is a display protocol; deprecation results should mention
    # SPICE, VNC (its replacement), or display protocols.
    _DeprecationIntentBoost(
        detect=_detect_spice_intent,
        title_terms='SPICE OR VNC OR "display protocol"',
        content_terms='SPICE OR VNC OR "display protocol"',
    ),
    # VM queries need deprecation results about virt-manager, cockpit,
    # and virtualization tools, not Eclipse Vert.x or network teaming.
    _DeprecationIntentBoost(
        detect=_detect_vm_intent,
        title_terms='"virt-manager" OR virtualization OR cockpit OR "virtual machine"',
        content_terms='"virt-manager" OR cockpit OR virsh OR "virtual machine"',
    ),
]

# Boost weights for deprecation intent terms.
#
# IMPORTANT: Keep these at ^5/^3 or lower.  Higher values (e.g. ^30/^15)
# inflate deprecation query scores well above main query scores (~120),
# causing _filter_by_score to drop ALL main results because they fall
# below 45% of the inflated top score.  The score filter uses raw Solr
# scores which are NOT comparable across queries with different bq boosts.
#
# At ^5/^3, the intent boosts are strong enough to re-rank within the
# deprecation results (e.g. VM deprecation > Vert.x deprecation) without
# overwhelming the cross-query score comparison.
_DEP_TITLE_BOOST = 5
_DEP_CONTENT_BOOST = 3


def _apply_deprecation_intent_boosts(params: dict, query_lower: str) -> None:
    """Add topic-specific boosts to the deprecation query based on detected intent.

    Without intent-aware boosts, the deprecation query matches ANY content
    containing "deprecated" and "removed", pulling in noise results like
    Eclipse Vert.x release notes or network teaming deprecation notices for
    a VM management question.  Adding intent-specific bq ensures the
    deprecation query surfaces deprecation notices about the user's actual
    topic, not unrelated deprecation content.

    This function APPENDS to the existing bq (the base deprecation term
    boosts defined in _build_deprecation_query) rather than replacing it,
    so the original deprecation/removal title boosts remain active.

    First matching intent wins (evaluated in _DEPRECATION_INTENT_BOOSTS
    order).  Queries that match no intent are left unchanged.

    Token budget impact: without these boosts, the deprecation query
    contributes 2-3 completely off-topic results (e.g., Eclipse Vert.x
    migration guides) that waste ~2,000 chars of response budget.

    See functional tests RSPEED_2480 (VM management) and RSPEED_2481 (SPICE).
    """
    for boost in _DEPRECATION_INTENT_BOOSTS:
        if boost.detect(query_lower):
            existing_bq = params.get("bq", "")
            params["bq"] = (
                f"{existing_bq} "
                f"allTitle:({boost.title_terms})^{_DEP_TITLE_BOOST} "
                f"main_content:({boost.content_terms})^{_DEP_CONTENT_BOOST}"
            )
            return


# ---------------------------------------------------------------------------
# Chunk conversion
# ---------------------------------------------------------------------------

# Maximum characters of fallback content when highlighting returns no snippets.
_FALLBACK_MAX_CHARS = 600

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
) -> tuple[list[PortalChunk], bool]:
    """Execute the unified portal search pipeline.

    Cleans the query, fires main + deprecation queries in parallel, converts
    results to chunks, merges via RRF, deduplicates, and returns the top-N
    chunks plus a flag indicating whether deprecation content was found.

    Args:
        query: Raw user query string.
        client: Shared httpx.AsyncClient (typed as object to avoid httpx import
            at module level; the actual type is enforced by ``_solr_query``).
        solr_endpoint: Full Solr endpoint URL for the portal core.
        max_results: Maximum chunks to return after deduplication.

    Returns:
        Tuple of (top-N PortalChunk list, has_deprecation bool).
    """
    cleaned = _clean_query(query)
    query_lower = query.lower()

    main_params = _build_main_query(cleaned)
    _apply_intent_boosts(main_params, query_lower, cleaned)

    dep_params = _build_deprecation_query(cleaned)
    # Apply intent-aware boosts to the deprecation query so it finds
    # deprecation notices about the user's ACTUAL topic, not random
    # deprecation content.  Without this, a VM management query gets
    # Eclipse Vert.x and network teaming deprecation results that waste
    # ~2,000 chars of response budget (see RSPEED_2480).
    _apply_deprecation_intent_boosts(dep_params, query_lower)

    main_data, dep_data = await asyncio.gather(
        _solr_query(main_params, client=client, solr_endpoint=solr_endpoint),
        _solr_query(dep_params, client=client, solr_endpoint=solr_endpoint),
    )

    main_chunks = _docs_to_chunks(main_data, query)
    dep_chunks = _docs_to_chunks(dep_data, query)

    merged = _reciprocal_rank_fusion(main_chunks, dep_chunks)
    deduped = _deduplicate_by_parent(merged)

    top_n = deduped[:max_results]

    dep_parent_ids = {d.parent_id for d in dep_chunks if d.parent_id is not None}
    has_deprecation = any(c.parent_id in dep_parent_ids for c in top_n if c.parent_id is not None)

    logger.info(
        "Portal search: query=%r main=%d dep=%d merged=%d deduped=%d returned=%d",
        query,
        len(main_chunks),
        len(dep_chunks),
        len(merged),
        len(deduped),
        len(top_n),
    )

    return top_n, has_deprecation


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_KIND_LABELS: dict[str, str] = {
    "documentation": "Documentation",
    "solution": "Solution",
    "article": "Article",
    "Cve": "CVE",
    "Erratum": "Security Advisory",
    "access-drupal10-node-type-page": "Documentation",
}


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
        lines.append(f"Content: {chunk.chunk}")

    return "\n".join(lines), sort_key


def _format_portal_results(
    chunks: list[PortalChunk],
    has_deprecation: bool,
    query: str,
    max_response_chars: int,
) -> str:
    """Format all chunks into a final string response with budget enforcement.

    Renders each chunk via ``_format_portal_chunk()``, sorts by annotation
    priority (replacements first, EOL last), prepends a deprecation warning
    banner if any chunk triggered deprecation detection, and enforces the
    character budget via ``_select_within_budget()``.
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

    return output
