"""Query runner and shared constants for the portal-rag Solr core."""

import httpx

from ..config import logger
from .models import RagDocument, RagResponse

EMPTY_RAG_RESPONSE = RagResponse(num_found=0, docs=[])


def _parse_solr_response(data: dict) -> RagResponse:
    """Validate and parse raw Solr JSON into a RagResponse.

    Args:
        data: Parsed JSON dict from Solr.

    Returns:
        RagResponse with parsed docs, or empty RagResponse on validation failure.
    """
    if "error" in data:
        logger.error("RAG query Solr error: %s", data["error"])
        return RagResponse(num_found=0, docs=[])

    response_data = data.get("response")
    if not isinstance(response_data, dict):
        logger.error("RAG query unexpected structure: %s", list(data.keys()))
        return RagResponse(num_found=0, docs=[])

    num_found = response_data.get("numFound")
    docs = response_data.get("docs")
    if not isinstance(num_found, int) or not isinstance(docs, list):
        logger.error("RAG query unexpected response payload types")
        return RagResponse(num_found=0, docs=[])

    logger.info("RAG query returned %d result(s)", num_found)
    return RagResponse(
        num_found=num_found,
        docs=[RagDocument(**doc) for doc in docs],
    )


async def rag_query(endpoint: str, params: dict, client: httpx.AsyncClient) -> RagResponse:
    """Execute a query against the portal-rag Solr core and return parsed JSON.

    Args:
        endpoint: Solr endpoint URL.
        params: Query parameters (q, rows, etc.).
        client: Shared AsyncClient instance.

    Returns:
        RagResponse with parsed docs or empty RagResponse on error.

    Raises:
        httpx.TimeoutException: If query times out.
        httpx.ConnectError: If connection fails.
        httpx.HTTPStatusError: If HTTP status is not 2xx.
        httpx.RequestError: On other network requests.
    """
    full_params = {"wt": "json"} | params
    logger.info("RAG query: endpoint=%r q=%r", endpoint, params.get("q"))
    try:
        response = await client.get(endpoint, params=full_params)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        logger.warning("RAG query timed out: %r", endpoint)
        raise
    except httpx.HTTPStatusError as e:
        logger.error("RAG query HTTP error %d: %s", e.response.status_code, e.response.text[:200])
        raise
    except httpx.ConnectError as e:
        logger.error("RAG query connection error: %s", e)
        raise
    except httpx.RequestError as e:
        logger.error("RAG query request error: %s", e)
        raise
    except ValueError as e:
        logger.error("RAG query returned non-JSON response: %s", e)
        return RagResponse(num_found=0, docs=[])

    return _parse_solr_response(data)
