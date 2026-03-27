"""Portal core search for solutions and articles missing from the portal-rag core."""

import re
from typing import Literal

import httpx
from pydantic import ValidationError

from ..config import logger
from .models import PortalDocument, PortalResponse, RagDocument, RagResponse

PortalDocumentKind = Literal["solution", "article"]
DEFAULT_DOCUMENT_KINDS: tuple[PortalDocumentKind, ...] = ("solution", "article")

# Field list for solutions/articles from the portal core on the RAG container.
# Verified against live rhokp-rag container: view_uri and allTitle are never populated
# for solution/article docs. Schema ref: docs/OKP_RAG_EXPLORATION.md (Container Overview).
PORTAL_FL = "id,resourceName,title,main_content,url_slug,documentKind,heading_h1,heading_h2,lastModifiedDate,score"


def _extract_highlights(data: dict) -> dict[str, list[str]]:
    """Extract and flatten Solr highlighting into a doc_id-to-snippets mapping.

    Solr returns highlighting as ``{doc_id: {field: [snippets]}}``.  This
    flattens it to ``{doc_id: [snippets]}`` for the ``main_content`` field,
    validating types defensively so malformed payloads are silently skipped.

    Args:
        data: Full parsed Solr JSON response dict.

    Returns:
        Mapping of document IDs to their main_content highlight snippet lists.
    """
    raw_hl = data.get("highlighting")
    if raw_hl is None:
        return {}
    if not isinstance(raw_hl, dict):
        logger.error("Portal query unexpected highlighting payload type: %s", type(raw_hl).__name__)
        return {}
    highlights: dict[str, list[str]] = {}
    for doc_key, fields in raw_hl.items():
        if not isinstance(fields, dict):
            continue
        snippets = fields.get("main_content")
        if isinstance(snippets, list) and snippets:
            highlights[doc_key] = snippets
    return highlights


def _parse_portal_response(data: dict) -> PortalResponse:
    """Validate and parse raw Solr JSON into a PortalResponse.

    Extracts documents and highlighting data from the Solr response.

    Args:
        data: Parsed JSON dict from Solr.

    Returns:
        PortalResponse with parsed docs and highlights, or empty response on validation failure.
    """
    if "error" in data:
        logger.error("Portal query Solr error: %s", data["error"])
        return PortalResponse(num_found=0, docs=[])

    response_data = data.get("response")
    if not isinstance(response_data, dict):
        logger.error("Portal query unexpected structure: %s", list(data.keys()))
        return PortalResponse(num_found=0, docs=[])

    num_found = response_data.get("numFound")
    docs = response_data.get("docs")
    if not isinstance(num_found, int) or not isinstance(docs, list):
        logger.error("Portal query unexpected response payload types")
        return PortalResponse(num_found=0, docs=[])

    try:
        parsed_docs = [PortalDocument.model_validate(doc) for doc in docs]
    except (TypeError, ValidationError):
        logger.exception("Portal query returned invalid document payload")
        return PortalResponse(num_found=0, docs=[])

    logger.info("Portal query returned %d result(s)", num_found)
    return PortalResponse(num_found=num_found, docs=parsed_docs, highlights=_extract_highlights(data))


async def _portal_query(endpoint: str, params: dict, client: httpx.AsyncClient) -> PortalResponse:
    """Execute a query against the portal Solr core and return parsed results.

    Mirrors rag_query() from common.py but returns PortalResponse instead of
    RagResponse. Kept separate to avoid coupling portal search with the
    portal-rag chunk models.

    Args:
        endpoint: Solr endpoint URL.
        params: Query parameters (q, rows, fq, etc.).
        client: Shared AsyncClient instance.

    Returns:
        PortalResponse with parsed docs or empty response on error.

    Raises:
        httpx.TimeoutException: If query times out.
        httpx.ConnectError: If connection fails.
        httpx.HTTPStatusError: If HTTP status is not 2xx.
        httpx.RequestError: On other network errors.
    """
    full_params = {"wt": "json"} | params
    logger.info("Portal query: endpoint=%r q=%r", endpoint, params.get("q"))
    try:
        response = await client.get(endpoint, params=full_params)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        logger.warning("Portal query timed out: %r", endpoint)
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Portal query HTTP error %d: %s", e.response.status_code, e.response.text[:200])
        raise
    except httpx.ConnectError as e:
        logger.error("Portal query connection error: %s", e)
        raise
    except httpx.RequestError as e:
        logger.error("Portal query request error: %s", e)
        raise
    except ValueError as e:
        logger.error("Portal query returned non-JSON response: %s", e)
        return PortalResponse(num_found=0, docs=[])

    return _parse_portal_response(data)


async def portal_search(
    query: str,
    *,
    client: httpx.AsyncClient,
    solr_url: str,
    max_results: int = 10,
    document_kinds: list[PortalDocumentKind] | None = None,
    fl: str | None = None,
) -> PortalResponse:
    """Search the portal core for solutions and articles.

    Runs an eDisMax query against the portal core's /select handler, filtering
    to the specified document kinds (defaults to solution + article). These
    document types are absent from the portal-rag core and require querying the
    legacy portal core on the same RAG Solr instance.

    Args:
        query: Search query string.
        client: Shared AsyncClient instance.
        solr_url: Base Solr URL (e.g. 'http://localhost:8984').
        max_results: Maximum number of results to return (default 10).
        document_kinds: Document types to search (constrained to
            "solution" and "article"). Defaults to both. Use a
            single-element list to restrict to one type.
        fl: Field list to return from Solr (optional). If None, Solr
            handler defaults are used.

    Returns:
        PortalResponse with matching documents.
    """
    kinds = DEFAULT_DOCUMENT_KINDS if document_kinds is None else tuple(document_kinds)
    if not kinds:
        msg = "document_kinds must not be empty"
        raise ValueError(msg)
    kind_filter = "{!terms f=documentKind}" + ",".join(kinds)

    endpoint = f"{solr_url}/solr/portal/select"
    params: dict[str, str | int] = {
        "q": query,
        "defType": "edismax",
        "qf": "url_slug^20 title^15 main_content^10 heading_h2^3 heading_h1^3 all_content^2",
        "rows": max_results,
        "fq": kind_filter,
        # Highlighting: extract chunk-sized passages from main_content
        "hl": "on",
        "hl.fl": "main_content",
        "hl.method": "unified",
        "hl.snippets": "5",
        "hl.fragsize": "800",
        "hl.fragsizeIsMinimum": "false",
        "hl.fragAlignRatio": "0.5",
        "hl.bs.type": "SENTENCE",
        "hl.bs.language": "en",
        "hl.defaultSummary": "true",
        "hl.weightMatches": "true",
        "hl.maxAnalyzedChars": "512000",
        "hl.score.k1": "1.0",
        "hl.score.b": "0.65",
        "hl.score.pivot": "200",
    }
    if fl is not None:
        params["fl"] = fl
    return await _portal_query(endpoint, params, client)


def _build_headings(doc: PortalDocument) -> str | None:
    """Build a comma-separated headings string from portal document heading fields.

    Concatenates non-empty heading_h1 and heading_h2 lists into a single
    comma-separated string matching the headings format used by portal-rag chunks.

    Args:
        doc: PortalDocument to extract headings from.

    Returns:
        Comma-separated heading string, or None if no headings are present.
    """
    parts: list[str] = []
    if doc.heading_h1:
        parts.extend(doc.heading_h1)
    if doc.heading_h2:
        parts.extend(doc.heading_h2)
    return ", ".join(parts) if parts else None


def _build_url(doc: PortalDocument, base_url: str) -> str | None:
    """Build an access.redhat.com URL for a portal document.

    Constructs a full URL from the document's url_slug and documentKind.
    Returns None if url_slug is missing or documentKind is unrecognized.

    Args:
        doc: PortalDocument with url_slug and documentKind fields.
        base_url: Base URL (e.g. 'https://access.redhat.com').

    Returns:
        Full URL string, or None if URL cannot be constructed.
    """
    if not doc.url_slug:
        return None
    kind_path = {
        "solution": "solutions",
        "article": "articles",
    }
    path = kind_path.get(doc.documentKind or "", "")
    if not path:
        return None
    return f"{base_url}/{path}/{doc.url_slug}"


def portal_highlights_to_rag_results(
    response: PortalResponse,
    *,
    base_url: str = "https://access.redhat.com",
) -> RagResponse:
    """Convert portal search results with highlights into chunk-sized RagDocuments.

    Each highlight snippet from a portal document becomes a separate RagDocument,
    mimicking the chunked structure of portal-rag results. Documents without
    highlights are skipped entirely.

    Args:
        response: PortalResponse with highlights from portal_search().
        base_url: Base URL for constructing document links (default: access.redhat.com).

    Returns:
        RagResponse with one RagDocument per highlight snippet. Returns empty
        RagResponse if no documents have highlights.
    """
    all_docs: list[RagDocument] = []

    for portal_doc in response.docs:
        snippets = response.highlights.get(portal_doc.id or "", [])
        if not snippets:
            continue

        for i, snippet in enumerate(snippets):
            chunk = re.sub(r"<[^>]+>", "", snippet).strip()
            all_docs.append(
                RagDocument(
                    doc_id=f"{portal_doc.id}_hl_{i}",
                    parent_id=portal_doc.id,
                    title=portal_doc.title,
                    chunk=chunk,
                    chunk_index=i,
                    num_tokens=len(chunk.split()),
                    headings=_build_headings(portal_doc),
                    online_source_url=_build_url(portal_doc, base_url),
                    documentKind=portal_doc.documentKind,
                    score=portal_doc.score,
                )
            )

    return RagResponse(num_found=len(all_docs), docs=all_docs)
