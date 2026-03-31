"""MCP tool definitions for RHEL OKP knowledge base search."""

from urllib.parse import urlsplit

import httpx
from fastmcp import Context

from .config import logger
from .content import doc_uri, strip_boilerplate, truncate_content
from .portal import _format_portal_results, _run_portal_search
from .server import get_app_context, mcp
from .solr import _clean_query, _extract_relevant_section, _get_highlights, _solr_query


def _normalize_doc_id(doc_id: str) -> str:
    """Strip the access.redhat.com URL prefix so full URLs work as Solr lookups.

    search_portal formats results with full URLs (e.g.
    ``https://access.redhat.com/documentation/...``) but Solr stores path-based
    IDs.  LLMs naturally pass the visible URL to get_document, so this strips
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

    Solr ``id`` may carry an ``/index.html`` suffix that ``view_uri`` omits,
    so checking both fields ensures a match regardless of which form the
    caller provides.  The value is escaped to prevent Lucene query injection.
    """
    safe = _escape_solr_phrase(doc_id)
    return f'id:"{safe}" OR view_uri:"{safe}"'


@mcp.tool
async def search_portal(
    ctx: Context,
    query: str,
    max_results: int = 10,
) -> str:
    """Search Red Hat knowledge base: documentation, solutions, articles, CVEs, errata, and support policies.

    Use this tool when you need official Red Hat documentation, security advisories,
    or errata to answer accurately, especially for version-specific details,
    lifecycle dates, deprecation status, or changes after 2024. For well-known
    Linux concepts (e.g. vi commands, systemd units, common CLI tools) you may
    answer directly without searching.

    IMPORTANT - interpreting results:
    - Results marked 'Applicability: RHV only' apply to Red Hat Virtualization,
      NOT to standard RHEL KVM. Do not recommend RHV-only workarounds as the
      primary answer for RHEL questions.
    - If results conflict and one states a feature was deprecated or removed in
      RHEL, lead with the deprecation/removal. Mention other-product workarounds
      only as a brief secondary note.
    - When results list specific releases or dates (e.g. EUS availability per
      minor release), enumerate every release explicitly in your answer.
    """
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info("search_portal: query=%r max_results=%d", query, max_results)
    try:
        app = get_app_context(ctx)
        chunks, has_deprecation = await _run_portal_search(
            query,
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
            max_results=max_results,
        )
        return _format_portal_results(chunks, has_deprecation, query, app.max_response_chars)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query, exc_info=True)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.exception("Search failed for query: %r", query)
        return "No results found. The knowledge base may be temporarily unavailable."


_DOCUMENT_FL = (
    "id,allTitle,title,heading_h1,main_content,view_uri,documentKind,"
    "product,documentation_version,lastModifiedDate,"
    "cve_details,portal_synopsis,portal_summary"
)


async def _fetch_document_with_query(
    doc_id: str, query: str, client: httpx.AsyncClient | None = None, *, solr_endpoint: str
) -> dict:
    """Fetch a document by ID using edismax query mode with highlighting.

    Uses _solr_query so edismax and highlight parameters are applied.
    Returns the raw Solr response dict.
    """
    return await _solr_query(
        {
            "q": _clean_query(query),
            "fq": _doc_id_filter(doc_id),
            "fl": _DOCUMENT_FL,
            "rows": 1,
            "hl.snippets": "10",
            "hl.fragsize": "600",
        },
        client=client,
        solr_endpoint=solr_endpoint,
    )


async def _fetch_document_raw(doc_id: str, client: httpx.AsyncClient | None = None, *, solr_endpoint: str) -> dict:
    """Fetch a document by ID using a plain HTTP request, bypassing edismax defaults.

    Uses httpx directly rather than _solr_query to avoid injecting edismax
    and highlight parameters that are not appropriate for raw document retrieval.
    Returns the raw Solr response dict.
    """
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(
            solr_endpoint,
            params={
                "q": _doc_id_filter(doc_id),
                "wt": "json",
                "fl": _DOCUMENT_FL,
                "rows": 1,
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def _format_document(doc: dict, data: dict, doc_id: str, query: str, max_chars: int) -> str:
    """Format a fetched document into a readable string.

    Renders title, type, product/version, URL, synopsis/summary/CVE details,
    and content (highlights if available, otherwise extracted relevant section).
    Truncates final output to max_chars as a safety net.
    """
    view_uri = doc.get("view_uri", "")
    result = f"**{doc.get('allTitle', 'Untitled')}**"
    result += f"\nType: {doc.get('documentKind', 'Unknown')}"
    if doc.get("product"):
        result += f"\nProduct: {doc['product']}"
    if doc.get("documentation_version"):
        result += f" {doc['documentation_version']}"
    result += f"\nURL: https://access.redhat.com{doc_uri(doc)}"

    if doc.get("portal_synopsis"):
        result += f"\n\nSynopsis: {doc['portal_synopsis']}"
    if doc.get("portal_summary"):
        result += f"\n\nSummary: {doc['portal_summary']}"
    if doc.get("cve_details"):
        result += f"\n\nCVE Details: {doc['cve_details']}"
    if doc.get("main_content"):
        content = strip_boilerplate(doc["main_content"])
        if query:
            highlights = _get_highlights(data, view_uri, doc_id, query=query)
            if highlights:
                result += f"\n\nContent:\n{highlights}"
            else:
                result += f"\n\nContent:\n{_extract_relevant_section(content, query, max_sections=8)}"
        else:
            result += f"\n\nContent:\n{_extract_relevant_section(content, '', max_sections=8)}"
    return truncate_content(result, max_chars)


@mcp.tool
async def get_document(ctx: Context, doc_id: str, query: str = "") -> str:
    """Fetch full content of a specific document by its ID.

    Use the URL from search results as doc_id. Pass query (the original
    search question) to get BM25-scored relevant passages instead of raw truncated content.
    """
    doc_id = _normalize_doc_id(doc_id)
    logger.info("get_document: doc_id=%r query=%r", doc_id, query)
    try:
        app = get_app_context(ctx)
        if query:
            data = await _fetch_document_with_query(
                doc_id, query, client=app.http_client, solr_endpoint=app.solr_endpoint
            )
        else:
            data = await _fetch_document_raw(doc_id, client=app.http_client, solr_endpoint=app.solr_endpoint)

        docs = data["response"]["docs"]
        if not docs:
            return f"Document not found: {doc_id}"

        return await _format_document(docs[0], data, doc_id, query, app.max_response_chars)
    except httpx.TimeoutException:
        logger.warning("get_document timed out for doc_id=%r query=%r", doc_id, query, exc_info=True)
        return f"Unable to fetch document {doc_id} because the request timed out. Please try again."
    except (httpx.HTTPError, ValueError):
        logger.exception("get_document failed for doc_id=%r query=%r", doc_id, query)
        return f"Unable to fetch document {doc_id}. The knowledge base may be temporarily unavailable."


# KLUDGE: Gemini 2.5 Flash has a built-in code execution capability that it
# attempts to invoke even when not explicitly configured as an available tool.
# When invoked via OpenAI-compatible endpoints through llama-stack, this causes
# "RuntimeError: OpenAI response failed: Unsupported tool call: run_code" and
# returns HTTP 500 to the client.
#
# This placeholder tool prevents the crash by registering run_code as a valid
# (but non-functional) tool. When Gemini attempts code execution, it receives
# feedback that code execution is unavailable rather than causing a server error.
#
# Related issue: https://discuss.ai.google.dev/t/gemini-live-api-unexpectedly-invokes-execute-code-and-other-built-in-tools-even-when-not-configured/87603
@mcp.tool
async def run_code(ctx: Context, language: str, code: str) -> str:
    """Execute code in the specified language.

    NOTE: This is a placeholder tool. Code execution is not available in this environment.
    """
    logger.warning("PLACEHOLDER run_code tool was invoked: language=%r code_length=%d", language, len(code))
    return (
        "Code execution is not available in this environment. "
        "Please provide the answer or code example directly in your response as text, "
        "rather than attempting to execute code."
    )
