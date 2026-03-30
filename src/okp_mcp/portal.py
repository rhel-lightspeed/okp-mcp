"""Unified portal search: query builders, intent detection, and EOL product filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .content import doc_uri, strip_boilerplate
from .solr import _filter_rhv_sentences


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


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


_VM_INTENT_RE = re.compile(r"\b(?:vm|vms|virtual machine|virtualization|hypervisor)\b")
_RELEASE_DATE_INTENT_RE = re.compile(r"\b(?:release dates?|released|when was|general availability)\b")
_EUS_INTENT_RE = re.compile(r"\b(?:eus|extended update support)\b")


def _detect_vm_intent(query_lower: str) -> bool:
    """Return True if the lowercased query contains VM/virtualization keywords."""
    return bool(_VM_INTENT_RE.search(query_lower))


def _detect_release_date_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about release dates or when something was released."""
    return bool(_RELEASE_DATE_INTENT_RE.search(query_lower))


def _detect_eus_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about EUS or Extended Update Support."""
    return bool(_EUS_INTENT_RE.search(query_lower))


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

# Fields returned by the main (all-type) query.  Includes type-specific fields
# (cve_details, portal_synopsis, ...) so the chunk-conversion layer (Phase 2)
# can fall back to them when highlighting returns no snippets.
_MAIN_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,"
    "product,documentation_version,lastModifiedDate,score,"
    "cve_details,cve_threatSeverity,"
    "portal_synopsis,portal_summary,portal_severity,portal_advisory_type"
)

# Fields returned by the deprecation query (no CVE/errata-specific fields).
_DEPRECATION_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,product,documentation_version,lastModifiedDate,score"
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
        # Override the base default of "true".  CVE/errata main_content starts
        # with boilerplate; disabling defaultSummary forces the chunk layer to
        # fall back to type-specific fields (cve_details, portal_synopsis).
        "hl.defaultSummary": "false",
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

    Called after ``_build_main_query`` to layer on VM, EUS, or release-date
    boosts without complicating the base query builder.
    """
    if _detect_vm_intent(query_lower):
        params["bq"] = (
            'title:(cockpit OR virtualization OR "virt-manager")^15 main_content:(cockpit OR "cockpit-machines")^5'
        )
        params["hl.q"] = f"{cleaned_query} {_VM_HIGHLIGHT_TERMS}"

    if _detect_eus_intent(query_lower):
        params["bq"] = 'title:"Enhanced EUS"^100 title:"EUS FAQ"^80'
        params["hl.q"] = f"{cleaned_query} {_EUS_HIGHLIGHT_TERMS}"

    if _detect_release_date_intent(query_lower):
        params["bq"] = 'title:"Enterprise Linux Release Dates"^200 allTitle:"release dates"^30'


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


def _fallback_generic(doc: dict, highlights_data: dict) -> str:
    """Build fallback chunk text for a doc/solution/article without highlights.

    Tries ``_get_highlights``-style extraction from the Solr highlighting
    payload first, then falls back to cleaning ``main_content``.
    """
    # Check if there's raw main_content we can use
    mc = doc.get("main_content", "")
    if mc:
        return strip_boilerplate(mc)[:_FALLBACK_MAX_CHARS]
    return ""


def _docs_to_chunks(
    solr_response: dict,
    query: str,
) -> list[PortalChunk]:
    """Convert a Solr response with highlighting into a flat list of PortalChunk chunks.

    Each highlight snippet becomes a separate PortalChunk. Documents without
    highlights fall back to type-specific fields (``cve_details`` for CVEs,
    ``portal_synopsis`` for errata, first 600 chars of ``main_content`` for
    docs/solutions/articles).

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

        # Try highlight snippets first
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
            # Fallback: use type-specific fields
            if kind == "Cve":
                chunk_text = _fallback_cve(doc)
            elif kind == "Erratum":
                chunk_text = _fallback_errata(doc)
            else:
                chunk_text = _fallback_generic(doc, highlighting)

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
