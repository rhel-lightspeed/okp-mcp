"""MCP tool definitions for RHEL OKP knowledge base search."""

import asyncio
import re

import httpx
from fastmcp import Context

from .config import logger
from .content import strip_boilerplate
from .formatting import SORT_DEPRECATION, _format_result
from .server import get_app_context, mcp
from .solr import _clean_query, _extract_relevant_section, _get_highlights, _solr_query

_PRODUCT_ALIASES: dict[str, str] = {
    "RHEL": "Red Hat Enterprise Linux",
    "OCP": "Red Hat OpenShift Container Platform",
}

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


def _detect_vm_intent(query_lower: str) -> bool:
    """Return True if the lowercased query contains VM/virtualization keywords."""
    return any(kw in query_lower for kw in ["vm", "virtual machine", "virtualization", "vms", "hypervisor"])


def _detect_release_date_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about release dates or when something was released."""
    return any(kw in query_lower for kw in ["release date", "released", "when was", "general availability"])


def _build_search_queries(
    cleaned: str,
    original_query: str,
    product: str,
    version: str,
    max_results: int,
    vm_intent: bool,
    release_date_intent: bool,
) -> tuple[dict, dict, dict]:
    """Build the three Solr query parameter dicts for search_documentation.

    Returns (doc_params, sol_params, dep_params) ready to be passed to _solr_query().
    """
    product_fq = f'(product:"{product}" OR (*:* -product:[* TO *]))'
    eol_fq = " AND ".join(f'-product:"{p}"' for p in _EOL_PRODUCTS)
    doc_filters = ["documentKind:(documentation OR access-drupal10-node-type-page)", product_fq, eol_fq]
    sol_filters = ["documentKind:(solution OR article)", product_fq, eol_fq]

    if version:
        version_boost = f"documentation_version:{version}^10"
    else:
        # Default: boost RHEL 9/10 docs so they outrank older versions
        version_boost = "documentation_version:10^10 documentation_version:9^8"

    extra_bq = (
        'title:(cockpit OR virtualization OR "virt-manager")^15 main_content:(cockpit OR "cockpit-machines")^5'
        if vm_intent
        else ""
    )
    doc_bq = f'documentKind:access-drupal10-node-type-page^30 title:"{product}"^20 {version_boost} {extra_bq}'.strip()

    rqq_safe = re.sub(r'["\\\[\]{}()!^~*?:/]', " ", original_query).strip()
    doc_params = {
        "q": cleaned,
        "fq": doc_filters,
        "fl": (
            "id,allTitle,heading_h1,title,view_uri,product,"
            "documentation_version,documentKind,main_content,lastModifiedDate,score"
        ),
        "rows": max_results,
        "bf": "recip(ms(NOW,lastModifiedDate),3.16e-11,1,1)^0.3",
        "bq": doc_bq,
        "rq": "{!rerank reRankQuery=$rqq reRankDocs=200 reRankWeight=3}",
        "rqq": f'title:"{rqq_safe}"^10 main_content:"{rqq_safe}"^5',
        "hl.snippets": "10",
    }

    sol_bq = 'main_content:(deprecated OR removed OR unsupported OR "end of life" OR "no longer")^5'
    if vm_intent and extra_bq:
        sol_bq = f"{sol_bq} {extra_bq}"
    if release_date_intent:
        sol_bq = f'{sol_bq} allTitle:"release dates"^30 title:"release dates"^20'
    sol_params = {
        "q": cleaned,
        "fq": sol_filters,
        "fl": "id,allTitle,heading_h1,title,view_uri,product,documentKind,main_content,lastModifiedDate,score",
        "rows": max_results + 2,
        "bf": "recip(ms(NOW,lastModifiedDate),3.16e-11,1,1)^0.3",
        "bq": sol_bq,
        "rq": "{!rerank reRankQuery=$rqq reRankDocs=200 reRankWeight=2}",
        "rqq": (
            f'title:"{rqq_safe}"^10 '
            f'main_content:(deprecated OR removed OR unsupported OR "no longer" OR "{rqq_safe}")^3'
        ),
    }

    dep_bq = (
        'allTitle:(deprecated OR removed OR "no longer" OR "end of life")^20 '
        'main_content:(deprecated OR removed OR "no longer available")^10'
    )
    if vm_intent and extra_bq:
        dep_bq = f"{dep_bq} {extra_bq}"
    dep_params = {
        "q": f"{cleaned} deprecated removed",
        "fq": ["documentKind:(solution OR article OR documentation)", product_fq, eol_fq],
        "fl": "id,allTitle,heading_h1,title,view_uri,product,documentKind,main_content,lastModifiedDate,score",
        "rows": 3,
        "bq": dep_bq,
    }

    return doc_params, sol_params, dep_params


def _doc_uri(doc: dict) -> str | None:
    """Return the canonical URI for a Solr document."""
    return doc.get("view_uri") or doc.get("id")


async def _format_docs(docs: list[dict], data: dict, query: str) -> list[tuple[str, int]]:
    """Format a list of Solr docs into (text, sort_key) pairs."""
    return list(await asyncio.gather(*[_format_result(d, data, include_content=True, query=query) for d in docs]))


async def _collect_dep_pairs(
    dep_data: dict,
    seen_uris: set,
    query: str,
) -> list[tuple[str, int]]:
    """Format dep docs not already seen in doc/sol results."""
    pairs: list[tuple[str, int]] = []
    for d in dep_data["response"]["docs"]:
        uri = _doc_uri(d)
        if uri not in seen_uris:
            pairs.append(await _format_result(d, dep_data, include_content=True, query=query))
            seen_uris.add(uri)
    return pairs


async def _deduplicate_and_sort_results(
    doc_data: dict,
    sol_data: dict,
    dep_data: dict,
    query: str,
) -> tuple[list[str], list[str], bool]:
    """Deduplicate results across doc/sol/dep buckets and sort by relevance.

    Returns (doc_results, sol_results, has_deprecation).
    """
    doc_pairs = await _format_docs(doc_data["response"]["docs"], doc_data, query)
    sol_pairs = await _format_docs(sol_data["response"]["docs"], sol_data, query)

    seen_uris = {_doc_uri(d) for d in doc_data["response"]["docs"]}
    seen_uris |= {_doc_uri(d) for d in sol_data["response"]["docs"]}
    dep_pairs = await _collect_dep_pairs(dep_data, seen_uris, query)

    sol_pairs.extend(dep_pairs)
    sol_pairs.sort(key=lambda pair: pair[1])

    has_deprecation = any(sk == SORT_DEPRECATION for _, sk in doc_pairs + sol_pairs)
    return [text for text, _ in doc_pairs], [text for text, _ in sol_pairs], has_deprecation


def _assemble_search_output(
    doc_results: list[str],
    sol_results: list[str],
    has_deprecation: bool,
    query: str,
) -> str:
    """Assemble the final output string from categorized search results.

    Returns a formatted string with documentation and solution sections,
    or an empty-results message when both lists are empty.
    """
    if not doc_results and not sol_results:
        return f"No results found for: {query}"

    output_parts = []
    if has_deprecation:
        output_parts.append(
            "\u26a0\ufe0f WARNING: Some results indicate a feature was deprecated or removed. "
            "If sources disagree, treat the deprecation/removal notice as authoritative "
            "over workarounds for other products."
        )
    if doc_results:
        output_parts.append(f"**Documentation** ({len(doc_results)} results):\n\n" + "\n\n---\n\n".join(doc_results))
    if sol_results:
        output_parts.append(
            f"**Solutions & Articles** ({len(sol_results)} results):\n\n" + "\n\n---\n\n".join(sol_results)
        )

    return "\n\n===\n\n".join(output_parts)


def _format_solution_article_doc(doc: dict, data: dict, query: str) -> str:
    """Format a single solution or article Solr document with title, URL, and highlights.

    Used by both search_solutions and search_articles since their document
    formatting is identical.
    """
    doc_id = doc.get("id", "")
    view_uri = doc.get("view_uri", "")
    title = doc.get("allTitle") or doc.get("heading_h1") or doc.get("title", "").split("|")[0].strip() or "Untitled"
    url_path = view_uri or doc_id
    highlights = _get_highlights(data, view_uri, doc_id, query=query)
    result = f"**{title}**"
    result += f"\nURL: https://access.redhat.com{url_path}"
    if highlights:
        result += f"\n> {highlights}"
    return result


def _format_errata_doc(doc: dict) -> str:
    """Format a single errata Solr document with type, severity, and synopsis."""
    result = f"**{doc.get('allTitle', 'Untitled')}**"
    if doc.get("portal_advisory_type"):
        result += f"\nType: {doc['portal_advisory_type']}"
    if doc.get("portal_severity"):
        result += f" | Severity: {doc['portal_severity']}"
    if doc.get("portal_synopsis"):
        result += f"\nSynopsis: {doc['portal_synopsis']}"
    result += f"\nURL: https://access.redhat.com{doc.get('view_uri', '')}"
    return result


@mcp.tool
async def search_documentation(
    ctx: Context,
    query: str,
    product: str = "",
    version: str = "",
    max_results: int = 5,
) -> str:
    """Search Red Hat knowledge base: documentation, solutions, articles, and support policies.

    This is the primary search tool. Always use this first for any RHEL question.
    Covers how-to guides, troubleshooting, deprecation notices, compatibility
    matrices, lifecycle policies, known issues, and best practices.

    IMPORTANT — interpreting results:
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
    logger.info(
        "search_documentation: query=%r product=%r version=%r max_results=%d", query, product, version, max_results
    )
    try:
        app = get_app_context(ctx)
        product = _PRODUCT_ALIASES.get(product, product) or "Red Hat Enterprise Linux"
        query_lower = query.lower()
        vm_intent = _detect_vm_intent(query_lower)
        release_date_intent = _detect_release_date_intent(query_lower)
        cleaned = _clean_query(query)
        doc_params, sol_params, dep_params = _build_search_queries(
            cleaned, query, product, version, max_results, vm_intent, release_date_intent
        )
        doc_data, sol_data, dep_data = await asyncio.gather(
            _solr_query(doc_params, client=app.http_client, solr_endpoint=app.solr_endpoint),
            _solr_query(sol_params, client=app.http_client, solr_endpoint=app.solr_endpoint),
            _solr_query(dep_params, client=app.http_client, solr_endpoint=app.solr_endpoint),
        )
        doc_results, sol_results, has_deprecation = await _deduplicate_and_sort_results(
            doc_data, sol_data, dep_data, query
        )
        return _assemble_search_output(doc_results, sol_results, has_deprecation, query)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


@mcp.tool
async def search_solutions(
    ctx: Context,
    query: str,
    product: str = "",
    max_results: int = 5,
) -> str:
    """Search Red Hat knowledge base solutions. Use for troubleshooting, error messages, and known issues."""
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info("search_solutions: query=%r product=%r max_results=%d", query, product, max_results)
    try:
        app = get_app_context(ctx)
        filters = ["documentKind:solution"]
        if product:
            filters.append(f"product:{product}")

        data = await _solr_query(
            {
                "q": query,
                "fq": filters,
                "fl": "id,allTitle,heading_h1,title,view_uri,url_slug,score",
                "rows": max_results,
            },
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
        )

        docs = data["response"]["docs"]
        if not docs:
            return f"No solutions found for: {query}"

        results = [_format_solution_article_doc(doc, data, query) for doc in docs]
        return f"Found {len(docs)} solutions for '{query}':\n\n" + "\n\n---\n\n".join(results)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


@mcp.tool
async def search_cves(
    ctx: Context,
    query: str,
    severity: str = "",
    max_results: int = 5,
) -> str:
    """Search CVE security advisories.

    Use for vulnerability information affecting Red Hat products.
    Severity values: Critical, Important, Moderate, Low.
    """
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info("search_cves: query=%r severity=%r max_results=%d", query, severity, max_results)
    try:
        app = get_app_context(ctx)
        filters = ["documentKind:Cve"]
        if severity:
            filters.append(f"cve_threatSeverity:{severity}")

        data = await _solr_query(
            {
                "q": query,
                "fq": filters,
                "fl": "allTitle,view_uri,cve_details,cve_threatSeverity,score",
                "rows": max_results,
            },
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
        )

        docs = data["response"]["docs"]
        if not docs:
            return f"No CVEs found for: {query}"

        results = []
        for doc in docs:
            result = f"**{doc.get('allTitle', 'Unknown CVE')}**"
            if doc.get("cve_threatSeverity"):
                result += f"\nSeverity: {doc['cve_threatSeverity']}"
            if doc.get("cve_details"):
                detail = doc["cve_details"][:300]
                result += f"\nDetails: {detail}"
            result += f"\nURL: https://access.redhat.com{doc.get('view_uri', '')}"
            results.append(result)

        return f"Found {len(docs)} CVEs for '{query}':\n\n" + "\n\n---\n\n".join(results)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


@mcp.tool
async def search_errata(
    ctx: Context,
    query: str,
    severity: str = "",
    advisory_type: str = "",
    max_results: int = 5,
) -> str:
    """Search Red Hat errata (security advisories, bug fixes, enhancements).

    Advisory types: Security Advisory, Bug Fix Advisory, Enhancement Advisory.
    """
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info(
        "search_errata: query=%r severity=%r type=%r max_results=%d", query, severity, advisory_type, max_results
    )
    try:
        app = get_app_context(ctx)
        filters = ["documentKind:Errata"]
        if severity:
            filters.append(f"portal_severity:{severity}")
        if advisory_type:
            filters.append(f"portal_advisory_type:{advisory_type}")

        data = await _solr_query(
            {
                "q": query,
                "fq": filters,
                "fl": "allTitle,view_uri,portal_severity,portal_advisory_type,portal_synopsis,score",
                "rows": max_results,
            },
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
        )

        docs = data["response"]["docs"]
        if not docs:
            return f"No errata found for: {query}"

        results = [_format_errata_doc(doc) for doc in docs]
        return f"Found {len(docs)} errata for '{query}':\n\n" + "\n\n---\n\n".join(results)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


@mcp.tool
async def search_articles(
    ctx: Context,
    query: str,
    max_results: int = 5,
) -> str:
    """Search Red Hat knowledge base articles. Use for general technical information, best practices, and tips."""
    if not query or not query.strip():
        return "Please provide a search query."
    max_results = max(1, min(max_results, 20))
    logger.info("search_articles: query=%r max_results=%d", query, max_results)
    try:
        app = get_app_context(ctx)
        data = await _solr_query(
            {
                "q": query,
                "fq": "documentKind:article",
                "fl": "id,allTitle,heading_h1,title,view_uri,url_slug,score",
                "rows": max_results,
            },
            client=app.http_client,
            solr_endpoint=app.solr_endpoint,
        )

        docs = data["response"]["docs"]
        if not docs:
            return f"No articles found for: {query}"

        results = [_format_solution_article_doc(doc, data, query) for doc in docs]
        return f"Found {len(docs)} articles for '{query}':\n\n" + "\n\n---\n\n".join(results)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


_DOCUMENT_FL = (
    "allTitle,main_content,view_uri,documentKind,"
    "product,documentation_version,"
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
            "fq": f'id:"{doc_id}"',
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
                "q": f'id:"{doc_id}"',
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


async def _format_document(doc: dict, data: dict, doc_id: str, query: str) -> str:
    """Format a fetched document into a readable string.

    Renders title, type, product/version, URL, synopsis/summary/CVE details,
    and content (highlights if available, otherwise extracted relevant section).
    """
    view_uri = doc.get("view_uri", "")
    result = f"**{doc.get('allTitle', 'Untitled')}**"
    result += f"\nType: {doc.get('documentKind', 'Unknown')}"
    if doc.get("product"):
        result += f"\nProduct: {doc['product']}"
    if doc.get("documentation_version"):
        result += f" {doc['documentation_version']}"
    result += f"\nURL: https://access.redhat.com{view_uri}"

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
                result += f"\n\nContent:\n{_extract_relevant_section(content, query)}"
        else:
            result += f"\n\nContent:\n{_extract_relevant_section(content, '')}"
    return result


@mcp.tool
async def get_document(ctx: Context, doc_id: str, query: str = "") -> str:
    """Fetch full content of a specific document by its ID.

    Use view_uri values from search results as doc_id. Pass query (the original
    search question) to get BM25-scored relevant passages instead of raw truncated content.
    """
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

        return await _format_document(docs[0], data, doc_id, query)
    except httpx.TimeoutException:
        logger.warning("Search timed out for query: %r", query)
        return "The search timed out. Please try again with a simpler query."
    except (httpx.HTTPError, ValueError):
        logger.error("Search failed for query: %r", query, exc_info=True)
        return "No results found. The knowledge base may be temporarily unavailable."


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
    logger.warning("⚠️  PLACEHOLDER run_code tool was invoked: language=%r code_length=%d", language, len(code))
    return (
        "Code execution is not available in this environment. "
        "Please provide the answer or code example directly in your response as text, "
        "rather than attempting to execute code."
    )
