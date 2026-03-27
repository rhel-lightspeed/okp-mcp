"""Portal core search for solutions and articles missing from the portal-rag core."""

from typing import Literal

import httpx
from pydantic import ValidationError

from ..config import logger
from .models import PortalDocument, PortalResponse

PortalDocumentKind = Literal["solution", "article"]
DEFAULT_DOCUMENT_KINDS: tuple[PortalDocumentKind, ...] = ("solution", "article")

# Field list for solutions/articles from the portal core on the RAG container.
# Verified against live rhokp-rag container: view_uri and allTitle are never populated
# for solution/article docs. Schema ref: docs/OKP_RAG_EXPLORATION.md (Container Overview).
PORTAL_FL = "id,resourceName,title,main_content,url_slug,documentKind,heading_h1,heading_h2,lastModifiedDate,score"


def _parse_portal_response(data: dict) -> PortalResponse:
    """Validate and parse raw Solr JSON into a PortalResponse.

    Args:
        data: Parsed JSON dict from Solr.

    Returns:
        PortalResponse with parsed docs, or empty response on validation failure.
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
    return PortalResponse(num_found=num_found, docs=parsed_docs)


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
    }
    if fl is not None:
        params["fl"] = fl
    return await _portal_query(endpoint, params, client)
