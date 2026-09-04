"""Document retrieval MCP tool and supporting helpers."""

import logging
import time

from urllib.parse import urlsplit

import httpx

from fastmcp import Context

from okp_mcp.content import _select_within_budget
from okp_mcp.content import doc_uri
from okp_mcp.content import format_sections
from okp_mcp.content import strip_boilerplate
from okp_mcp.content import truncate_content
from okp_mcp.metrics import DOCUMENT_HIGHLIGHT_FALLBACK
from okp_mcp.metrics import DOCUMENT_HIGHLIGHT_USED
from okp_mcp.metrics import DOCUMENT_NOT_FOUND
from okp_mcp.metrics import DOCUMENT_NUDGE
from okp_mcp.metrics import DOCUMENT_TOC_PASSAGES_DROPPED
from okp_mcp.metrics import TOOL_CALLS
from okp_mcp.metrics import TOOL_DURATION
from okp_mcp.outline import DocumentOutline
from okp_mcp.outline import NO_OUTLINE
from okp_mcp.server import get_app_context
from okp_mcp.server import mcp
from okp_mcp.solr import _clean_query
from okp_mcp.solr import _extract_relevant_section
from okp_mcp.solr import _get_highlight_snippets
from okp_mcp.solr import _solr_query
from okp_mcp.tools.shared import DOCUMENT_FL
from okp_mcp.types import SolrDoc
from okp_mcp.types import SolrResponse


logger = logging.getLogger("okp_mcp.tools.get_document")

# Documentation pages (RHEL guides, etc.) can exceed 500KB of raw content.
# Tighter budgets here prevent token-heavy responses while still surfacing
# the most relevant passages via Solr highlights or local BM25 extraction.
_DOCUMENTATION_MAX_CHARS = 10_000

# Highlights asked of Solr, and the most rendered per document.
#
# Asking for more to compensate for the table-of-contents runs that get dropped
# was tried and rejected: hl.snippets does not return a longer list of the same
# fragments, it re-fragments the field. Raising the ask from 10 to 24 on
# "what are webhook admission plugins" pushed the three admission passages out
# of the top three and filled those slots with the glossary and Ignition
# instead. Aggregate passage and character counts improved while the answer got
# worse, so this number stays where Solr's own selection is best.
_MAX_PASSAGES = 10
_DOCUMENTATION_MAX_SECTIONS = 3
_DOCUMENTATION_PER_SECTION = 1000


def _normalize_doc_id(doc_id: str) -> str:
    """Strip the access.redhat.com URL prefix so full URLs work as Solr lookups.

    search_portal formats results with full URLs (e.g.
    ``https://access.redhat.com/documentation/...``) but Solr stores path-based
    IDs. LLMs naturally pass the visible URL to get_document, so this strips
    the prefix to recover the path.

    Uses proper URL parsing to reject lookalike domains (e.g.
    ``access.redhat.com.evil.tld``) and strip query/fragment parts.
    """
    parsed = urlsplit(doc_id)
    if parsed.scheme in {"http", "https"} and parsed.netloc == "access.redhat.com":
        return parsed.path or "/"
    return doc_id


def _escape_solr_phrase(value: str) -> str:
    """Escape characters that are unsafe inside a quoted Solr/Lucene phrase."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _doc_id_filter(doc_id: str) -> str:
    """Build a Solr filter that matches a document by ``id`` or ``view_uri``.

    Both fields are checked in both suffix forms, because the corpus uses
    three different conventions and callers legitimately supply any of them:

    - solutions/articles/documentation carry ``id`` with an ``/index.html``
      suffix and no ``view_uri`` at all, yet search_portal renders their URL
      with the suffix stripped -- so the visible URL round-trips only if the
      suffix is restored here.
    - errata carry ``id`` as the bare advisory ID (``RHSA-2026:29976``) and
      ``view_uri`` as ``/errata/RHSA-2026:29976/``; appending ``/index.html``
      to an erratum matches neither field.
    - CVEs carry both forms.

    The value is escaped to prevent Lucene query injection.
    """
    safe = _escape_solr_phrase(doc_id.removesuffix("/index.html").removesuffix("/"))
    return f'id:"{safe}" OR id:"{safe}/index.html" OR view_uri:"{safe}" OR view_uri:"{safe}/"'


def _uses_document_passages(doc: SolrDoc) -> bool:
    """Return whether a document should render Solr highlights as passages."""
    if doc.documentKind == "documentation":
        return True
    return doc_uri(doc).startswith("/documentation/")


def _format_metadata(doc: SolrDoc) -> str:
    """Build the metadata header for a fetched document."""
    result = f"**{doc.allTitle or 'Untitled'}**"
    result += f"\nType: {doc.documentKind or 'Unknown'}"
    if doc.product:
        result += f"\nProduct: {doc.product}"
    if doc.documentation_version:
        result += f" {doc.documentation_version}"
    result += f"\nURL: https://access.redhat.com{doc_uri(doc)}"

    if doc.portal_synopsis:
        result += f"\n\nSynopsis: {doc.portal_synopsis}"
    if doc.portal_summary:
        result += f"\n\nSummary: {doc.portal_summary}"
    if doc.cve_details:
        result += f"\n\nCVE Details: {doc.cve_details}"
    return result


def _drop_toc_passages(snippets: list[str], outline: DocumentOutline) -> list[str]:
    """Drop highlights that are table-of-contents runs rather than prose.

    Solr highlights ``main_content``, which leads with the page's ToC, so a
    quarter of the passages come back as strings of headings. They are also the
    long ones -- measured at 26% of passages but 59% of the characters -- so
    they crowd real prose out of the passage budget rather than merely sitting
    beside it. Dropping them leaves exactly the prose Solr already chose, in
    Solr's order, and returns the budget they were occupying.

    Filtering needs the mirror: without it every passage looks unplaceable, so
    an unavailable mirror must leave the list alone rather than empty it. The
    same guard covers a page whose passages are all classified as ToC, where
    returning nothing would be worse than returning the headings.
    """
    if not outline.starts:
        return snippets

    prose = [snippet for snippet in snippets if outline.locate(snippet) is not None]
    if not prose:
        return snippets

    DOCUMENT_TOC_PASSAGES_DROPPED.inc(len(snippets) - len(prose))
    return prose


def _passage_label(index: int, snippet: str, outline: DocumentOutline) -> str:
    """Label a passage, naming the section it came from when that is known.

    A passage with no home section is a table-of-contents fragment: Solr
    highlights ``main_content``, which leads with the page's ToC, so those
    runs of headings match no body text. They keep the bare label rather than
    borrowing a neighbouring section's anchor.
    """
    section = outline.locate(snippet)
    if section is None:
        return f"Passage {index}:"
    return f"Passage {index} [#{section.anchor} — {section.title}]:"


def _format_document_passages(
    highlight_snippets: list[str],
    query: str,
    max_chars: int,
    current_result: str,
    outline: DocumentOutline = NO_OUTLINE,
) -> str:
    """Format highlight snippets as numbered passages within the remaining budget."""
    header = "\n\nRelevant passages:\n"
    if outline.starts:
        header = "\n\nRelevant passages (append a passage's fragment to the URL above to link to its section):\n"

    remaining_budget = max_chars - len(current_result) - len(header)
    if remaining_budget <= 0:
        return ""

    formatted_passages = [
        f"{_passage_label(index + 1, snippet, outline)}\n{snippet}" for index, snippet in enumerate(highlight_snippets)
    ]
    passages = _select_within_budget(formatted_passages, remaining_budget, query)
    return f"{header}{passages}"


def _format_document_content(
    doc: SolrDoc,
    data: SolrResponse,
    doc_id: str,
    query: str,
    max_chars: int,
    current_result: str,
    outline: DocumentOutline = NO_OUTLINE,
) -> str:
    """Build the content section for a fetched document.

    Documentation pages (RHEL guides, etc.) can exceed 500KB and eat tokens
    fast, so they get tighter budgets. Non-documentation types pass through
    with the full budget.

    Decision tree for documentation pages::

        is documentation?
         +--NO--> [unchanged: full content, 30K budget]
         |
        YES
         |
        query provided?
         +--NO--> metadata + section outline + "pass a query" nudge
         |
        YES
         |
        Solr highlights exist?
         +--YES--> highlight passages (capped at 10K)
         +--NO---> BM25 fallback (3 sections, 1K each, ~3-5K typical)
    """
    is_documentation = _uses_document_passages(doc)

    # Documentation without a query is almost always useless (first 1500 chars
    # is typically a table of contents). Nudge the caller to be specific, and
    # list the sections so the nudge is actionable: the caller can pick the
    # section it wants, link straight to it, and phrase the follow-up query
    # from the real title rather than guessing at what the guide covers.
    if is_documentation and not query:
        DOCUMENT_NUDGE.inc()
        listing = format_sections(doc, outline.sections, url=f"https://access.redhat.com{doc_uri(doc)}")
        return listing + (
            "\n\nThis is a large documentation page. "
            "Pass a query to get_document to extract the most relevant passages."
        )

    main_content = doc.main_content
    if not main_content:
        return ""

    content = strip_boilerplate(main_content)
    if not query:
        return f"\n\nContent:\n{_extract_relevant_section(content, '', max_sections=8)}"

    highlight_snippets = _get_highlight_snippets(data, doc.view_uri, doc.id, doc_id, query=query)

    if is_documentation:
        if highlight_snippets:
            DOCUMENT_HIGHLIGHT_USED.inc()
            doc_budget = min(max_chars, _DOCUMENTATION_MAX_CHARS)
            passages = _drop_toc_passages(highlight_snippets, outline)[:_MAX_PASSAGES]
            return _format_document_passages(passages, query, doc_budget, current_result, outline)
        DOCUMENT_HIGHLIGHT_FALLBACK.inc()
        extracted = _extract_relevant_section(
            content, query, per_section=_DOCUMENTATION_PER_SECTION, max_sections=_DOCUMENTATION_MAX_SECTIONS
        )
        return f"\n\nContent:\n{extracted}"

    if not highlight_snippets:
        DOCUMENT_HIGHLIGHT_FALLBACK.inc()
        return f"\n\nContent:\n{_extract_relevant_section(content, query, max_sections=8)}"
    DOCUMENT_HIGHLIGHT_USED.inc()
    # Non-documentation kinds render every snippet inline, so they keep the
    # pre-overfetch count rather than growing with the larger Solr ask.
    return f"\n\nContent:\n{' ... '.join(highlight_snippets[:_MAX_PASSAGES])}"


async def _fetch_document_with_query(
    doc_id: str,
    query: str,
    client: httpx.AsyncClient,
    *,
    solr_endpoint: str,
) -> SolrResponse:
    """Fetch a document by ID, using the caller's query only to pick highlights.

    Identity and relevance are kept in separate parameters on purpose. The
    document is selected by ``q`` under the lucene parser, and the caller's
    query is passed as ``hl.q`` so it chooses which passages come back
    without deciding whether the document is returned at all.

    Putting the caller's query in ``q`` instead would subject retrieval to
    the edismax ``mm`` in _SOLR_BASE_PARAMS: a perfectly valid doc_id whose
    document happens to share too few terms with the query would match zero
    rows and be reported as "Document not found". Returns the Solr response.
    """
    # defType=lucene overrides the edismax default from _SOLR_BASE_PARAMS. The
    # base edismax boost params (qf/pf/mm/ps) still ride along in the merged
    # request and appear in the SOLR query log, but Solr silently ignores them
    # under the lucene parser -- they are inert here, not a bug. They are left
    # in place rather than filtered out because _solr_query is shared with
    # search_portal, which needs them; stripping them would risk that path for a
    # purely cosmetic log cleanup.
    return await _solr_query(
        {
            "q": _doc_id_filter(doc_id),
            "defType": "lucene",
            "hl.q": _clean_query(query),
            "hl.qparser": "edismax",
            "fl": DOCUMENT_FL,
            "rows": 1,
            "hl.snippets": str(_MAX_PASSAGES),
            "hl.fragsize": "600",
        },
        client=client,
        solr_endpoint=solr_endpoint,
    )


async def _fetch_document_raw(
    doc_id: str, client: httpx.AsyncClient | None = None, *, solr_endpoint: str
) -> SolrResponse:
    """Fetch a document by ID using a plain HTTP request, bypassing edismax defaults.

    Uses httpx directly rather than _solr_query to avoid injecting edismax
    and highlight parameters that are not appropriate for raw document retrieval.

    ``defType`` is pinned to lucene because bypassing _solr_query does not
    bypass the Solr request handler's own defaults, which select edismax.
    Under edismax the field-qualified OR in _doc_id_filter is treated as a
    set of optional clauses governed by the handler's ``mm``, so every
    lookup matched zero documents. Returns the Solr response.
    """
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(
            solr_endpoint,
            params={
                "q": _doc_id_filter(doc_id),
                "defType": "lucene",
                "wt": "json",
                "fl": DOCUMENT_FL,
                "rows": 1,
            },
        )
        response.raise_for_status()
        return SolrResponse.model_validate(response.json())
    finally:
        if close_client:
            await client.aclose()


async def _format_document(
    doc: SolrDoc,
    data: SolrResponse,
    doc_id: str,
    query: str,
    max_chars: int,
    outline: DocumentOutline = NO_OUTLINE,
) -> str:
    """Format a fetched document into a readable string.

    Renders title, type, product/version, URL, synopsis/summary/CVE details,
    and content (highlights if available, otherwise extracted relevant section).
    Truncates final output to max_chars as a safety net.
    """
    result = _format_metadata(doc)
    result += _format_document_content(doc, data, doc_id, query, max_chars, result, outline)
    return truncate_content(result, max_chars)


@mcp.tool
async def get_document(ctx: Context, doc_id: str, query: str = "") -> str:
    """Fetch full content of a specific document by its ID.

    Use the URL from search results as doc_id. Pass query (the original
    search question) to get BM25-scored relevant passages instead of raw truncated content.
    """
    TOOL_CALLS.labels(tool="get_document").inc()
    _start = time.monotonic()

    doc_id = _normalize_doc_id(doc_id)
    logger.info("get_document: doc_id=%r has_query=%s", doc_id, bool(query))
    try:
        app = get_app_context(ctx)
        if query:
            data = await _fetch_document_with_query(
                doc_id, query, client=app.http_client, solr_endpoint=app.solr_endpoint
            )
        else:
            data = await _fetch_document_raw(doc_id, client=app.http_client, solr_endpoint=app.solr_endpoint)

        docs = data.response.docs
        if not docs:
            DOCUMENT_NOT_FOUND.inc()
            return f"Document not found: {doc_id}"

        doc = docs[0]
        # Both documentation paths spend the anchors: the no-query path lists
        # them, the query path attaches them to passages. Other document kinds
        # have no mirror page, so they never pay the ~200KB fetch.
        outline = NO_OUTLINE
        if _uses_document_passages(doc) and app.outline_fetcher is not None:
            outline = await app.outline_fetcher.get(doc.id or doc_id)

        return await _format_document(doc, data, doc_id, query, app.max_response_chars, outline)
    except httpx.TimeoutException:
        logger.warning("get_document timed out for doc_id=%r", doc_id, exc_info=True)
        return f"Unable to fetch document {doc_id} because the request timed out. Please try again."
    except (httpx.HTTPError, ValueError):
        logger.exception("get_document failed for doc_id=%r", doc_id)
        return f"Unable to fetch document {doc_id}. The knowledge base may be temporarily unavailable."
    finally:
        TOOL_DURATION.labels(tool="get_document").observe(time.monotonic() - _start)
