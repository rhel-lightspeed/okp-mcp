"""Query runner and shared constants for the portal-rag Solr core."""

import re

import httpx

from ..config import STOP_WORDS, logger
from .models import RagDocument, RagResponse

EMPTY_RAG_RESPONSE = RagResponse(num_found=0, docs=[])


def _split_quoted_and_plain(text: str) -> list[str]:
    """Split text into an ordered list of tokens: raw words and quoted phrases.

    Quoted phrases are preserved as-is (including the double-quote delimiters).
    Empty quoted phrases ('""') are skipped.
    Unmatched opening quotes cause remaining text to be treated as plain words.
    """
    tokens: list[str] = []
    remainder = text
    while '"' in remainder:
        before, _, rest = remainder.partition('"')
        tokens.extend(before.split())
        if '"' not in rest:
            tokens.extend(rest.split())
            remainder = ""
            break
        phrase, _, remainder = rest.partition('"')
        if phrase:
            tokens.append(f'"{phrase}"')
    tokens.extend(remainder.split())
    return tokens


def _quote_hyphenated_compounds(tokens: list[str]) -> list[str]:
    """Wrap hyphenated compound terms in double quotes for SOLR phrase matching.

    Solr's standard tokenizer splits on hyphens, so ``rpm-ostree`` becomes two
    independent tokens ``rpm`` and ``ostree``.  Quoting forces Solr to match the
    full compound as a phrase, preventing generic ``rpm`` matches from drowning
    out specific ``rpm-ostree`` content.

    Already-quoted tokens and short fragments (<=3 chars) are left untouched.
    """
    return [f'"{t}"' if "-" in t and not t.startswith('"') and len(t) > 3 else t for t in tokens]


_TERM_TRIM_CHARS = "?.,!"


def _normalize_query_token(token: str) -> str:
    """Strip trailing punctuation and lowercase a query token for BM25 matching."""
    return token.lower().strip(_TERM_TRIM_CHARS)


def _is_numeric(token: str) -> bool:
    """Return True for numeric version tokens (e.g. '10', '9', '9.4', '4.16')."""
    return bool(re.fullmatch(r"\d+(?:\.\d+)*", _normalize_query_token(token)))


def clean_rag_query(query: str) -> str:
    """Clean a query string for RAG Solr search.

    Strips English stopwords, quotes hyphenated compounds for phrase
    matching, and preserves numeric tokens (e.g. version numbers).
    Falls back to the original query if all tokens are stopwords.

    Args:
        query: Raw user query string.

    Returns:
        Cleaned query string optimized for Solr eDisMax search.
    """
    tokens = _split_quoted_and_plain(query)
    parts = [
        t
        for t in tokens
        if t.startswith('"')
        or _is_numeric(t)
        or (_normalize_query_token(t) and _normalize_query_token(t) not in STOP_WORDS)
    ]
    parts = _quote_hyphenated_compounds(parts)
    return " ".join(parts) if parts else query


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
