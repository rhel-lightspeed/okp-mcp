"""Query runner and shared constants for the portal-rag Solr core."""

import httpx

from ..config import logger


def _empty_rag_response() -> dict:
    """Return a fresh empty RAG response dict (avoids shared-mutable-state bugs)."""
    return {"response": {"numFound": 0, "docs": []}}


EMPTY_RAG_RESPONSE: dict = _empty_rag_response()


async def rag_query(endpoint: str, params: dict, client: httpx.AsyncClient) -> dict:
    """Execute a query against the portal-rag Solr core and return parsed JSON.

    Args:
        endpoint: Solr endpoint URL.
        params: Query parameters (q, rows, etc.).
        client: Shared AsyncClient instance.

    Returns:
        Parsed JSON response or EMPTY_RAG_RESPONSE on error.

    Raises:
        httpx.TimeoutException: If query times out.
        httpx.ConnectError: If connection fails.
        httpx.HTTPStatusError: If HTTP status is not 2xx.
        httpx.RequestError: On other network errors.
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
        return _empty_rag_response()

    if "error" in data:
        logger.error("RAG query Solr error: %s", data["error"])
        return _empty_rag_response()
    if "response" not in data or not isinstance(data.get("response", {}).get("docs"), list):
        logger.error("RAG query unexpected structure: %s", list(data.keys()))
        return _empty_rag_response()

    logger.info("RAG query returned %d result(s)", data["response"]["numFound"])
    return data
