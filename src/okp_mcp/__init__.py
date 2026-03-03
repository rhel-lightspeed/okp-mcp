"""OKP MCP Server - Search Red Hat documentation via Solr RAG."""

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from .embeddings import EmbeddingModel
from .models import Chunk, CollectionInfo, Document, SampleDocument, SearchResult
from .solr import SolrClient

# Constants from okp_query.py:17-25
TOKEN_BUDGET = 2048
MIN_CHUNK_WINDOW = 4
MIN_CHUNK_GAP = 4

# Global model instance (loaded at startup)
embedding_model: EmbeddingModel | None = None


def expand_context_window(
    chunks: list[dict], match_index: int, token_budget: int = TOKEN_BUDGET
) -> list[dict]:
    """Expand context window around matched chunk.

    Bidirectionally expands from the matched chunk, respecting token budget.
    Algorithm copied from okp_query.py:54-89.

    Args:
        chunks: List of chunk dicts with 'chunk_index' and 'num_tokens'
        match_index: Index into chunks list of the matched chunk
        token_budget: Maximum tokens to include

    Returns:
        Selected chunks sorted by chunk_index
    """
    total_tokens = 0
    selected_chunks = []

    n = len(chunks)
    left = match_index
    right = match_index + 1

    # Always include the matched chunk first
    center_chunk = chunks[match_index]
    total_tokens += center_chunk["num_tokens"]
    selected_chunks.append(center_chunk)

    while total_tokens < token_budget and (left > 0 or right < n):
        added = False

        if left > 0:
            next_chunk = chunks[left - 1]
            if total_tokens + next_chunk["num_tokens"] <= token_budget:
                selected_chunks.insert(0, next_chunk)
                total_tokens += next_chunk["num_tokens"]
                left -= 1
                added = True

        if right < n:
            next_chunk = chunks[right]
            if total_tokens + next_chunk["num_tokens"] <= token_budget:
                selected_chunks.append(next_chunk)
                total_tokens += next_chunk["num_tokens"]
                right += 1
                added = True

        if not added:
            break

    return sorted(selected_chunks, key=lambda c: c["chunk_index"])


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Load embedding model at startup."""
    global embedding_model
    embedding_model = EmbeddingModel()
    yield


mcp = FastMCP(
    "OKP Knowledge Portal",
    instructions="""This server searches Red Hat Enterprise Linux (RHEL) documentation,
errata (RHSA/RHBA/RHEA advisories), and CVE security vulnerability details
indexed in a Solr collection.

Search strategy:
1. Start with explore_collection() to understand what content is available.
2. Use semantic_search for conceptual/how-to questions in natural language.
3. Use hybrid_search for specific terms (CVE IDs, package names, error messages,
   CLI commands like systemctl/dnf/semanage).
4. Pass a list of strings to either search tool to search from multiple angles
   and improve recall.
5. Always cite the reference_url from results so users can verify the source.""",
    lifespan=lifespan,
)


async def _process_chunk_hits(
    chunk_hits: list[dict], include_original_score: bool = False
) -> list[Document]:
    """Process chunk hits into Documents with deduplication and context expansion.

    Shared logic for semantic and hybrid search.

    Args:
        chunk_hits: Raw chunk hits from Solr (sorted by score desc)
        include_original_score: Whether to include original_score field

    Returns:
        List of Document objects
    """
    docs = []
    kept_indices_by_parent: dict[str, list[int]] = defaultdict(list)

    for match in chunk_hits:
        parent_id = match["parent_id"]
        matched_chunk_index = match["chunk_index"]

        # Skip if too close to ANY already-kept anchor in this parent
        if any(
            abs(matched_chunk_index - kept) < MIN_CHUNK_GAP
            for kept in kept_indices_by_parent[parent_id]
        ):
            continue

        # Keep this anchor
        kept_indices_by_parent[parent_id].append(matched_chunk_index)

        # Fetch parent metadata and chunks
        async with SolrClient() as client:
            parent_doc = await client.get_parent_metadata(parent_id)

            # Short doc handling (okp_query.py:161-174)
            if (
                parent_doc["total_chunks"] < MIN_CHUNK_WINDOW
                or parent_doc["total_tokens"] <= TOKEN_BUDGET
            ):
                all_chunks = await client.get_chunks(parent_id)
                selected = all_chunks
            else:
                # Bounded window around match (±10)
                window_start = max(0, matched_chunk_index - 10)
                if parent_doc["total_chunks"] > 0:
                    window_end = min(
                        parent_doc["total_chunks"] - 1, matched_chunk_index + 10
                    )
                else:
                    window_end = 0

                context_chunks = await client.get_chunks(
                    parent_id, window_start, window_end
                )

                # Find local match index in response
                match_pos = next(
                    i
                    for i, c in enumerate(context_chunks)
                    if c["chunk_index"] == matched_chunk_index
                )
                selected = expand_context_window(
                    context_chunks, match_pos, TOKEN_BUDGET
                )

        # Assemble the final Document
        text = "\n\n".join(c["chunk"] for c in selected)
        chunk_models = [
            Chunk(chunk_index=c["chunk_index"], chunk=c["chunk"]) for c in selected
        ]

        doc = Document(
            doc_id=parent_doc["doc_id"],
            title=parent_doc["title"],
            reference_url=parent_doc.get(
                "reference_url", f"/docs{parent_doc['doc_id']}"
            ),
            text=text,
            score=match["score"],
            original_score=match.get("originalScore()")
            if include_original_score
            else None,
            matched_chunk_index=matched_chunk_index,
            chunks=chunk_models,
        )
        docs.append(doc)

    return docs


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def semantic_search(
    query: Annotated[
        str | list[str],
        Field(
            description="Natural language question(s) about RHEL. "
            "Use a list to search from multiple angles for better recall. "
            "Examples: 'How do I configure SELinux on RHEL 9?' or "
            "['configure SELinux RHEL 9', 'SELinux setup and configuration']"
        ),
    ],
    max_results: Annotated[
        int,
        Field(
            description="Number of documents to return. Use 5 for focused "
            "queries, 10+ for broad research.",
            ge=1,
            le=20,
        ),
    ] = 5,
) -> SearchResult:
    """Search Red Hat documentation using semantic similarity.

    Best for conceptual questions and natural language queries where you're
    asking "how do I..." or "what is..." rather than searching for specific
    terms.

    USE THIS WHEN:
        - Asking conceptual "how do I..." or "what is..." questions
        - Query is natural language without specific identifiers
        - Looking for explanations, procedures, or best practices

    USE hybrid_search INSTEAD WHEN:
        - Query contains specific identifiers (CVE IDs, package names, commands)
        - Looking for exact error messages or log snippets
        - Searching for specific CLI commands

    Examples:
        - "How do I configure SELinux on RHEL 9?"
        - "What are the steps to set up a RAID array?"
        - "Explain firewalld zones and their purpose"
        - "How to join RHEL to Active Directory"
        - "Best practices for hardening SSH"

    MULTI-QUERY SUPPORT:
        Pass multiple query variations to improve recall when semantic matching
        is uncertain. Results are merged with max-score deduplication.

        Example - single query:
            semantic_search(query="configure SELinux RHEL 9")

        Example - multiple query variations:
            semantic_search(query=[
                "How do I configure SELinux on RHEL 9?",
                "SELinux setup and configuration",
                "Enable SELinux enforcing mode"
            ])

    Query tips:
        - Include RHEL version if relevant ("RHEL 10", "RHEL 9")
        - Be specific about what you want to accomplish
        - Use natural language, not keyword soup
        - For difficult matches, try 2-3 phrasings of the same concept

    Returns:
        SearchResult containing:
            - question: The original query (or list of queries)
            - docs: List of Document objects, each with:
                - title: Document title
                - reference_url: Link to official Red Hat docs
                - text: Relevant excerpt (context-expanded around match)
                - score: Relevance score (higher = more relevant)
                - chunks: Individual text chunks that matched
    """
    if embedding_model is None:
        raise ToolError(
            "Embedding model not initialized — server may still be starting up. Retry in a few seconds."
        )

    queries = [query] if isinstance(query, str) else query

    try:
        all_chunk_hits: dict[tuple[str, int], dict] = {}

        for q in queries:
            vector = embedding_model.encode(q)
            async with SolrClient() as client:
                chunk_hits = await client.semantic_search(vector, rows=max_results)

            for hit in chunk_hits:
                key = (hit["parent_id"], hit["chunk_index"])
                if (
                    key not in all_chunk_hits
                    or hit["score"] > all_chunk_hits[key]["score"]
                ):
                    all_chunk_hits[key] = hit

        merged_hits = list(all_chunk_hits.values())
        merged_hits.sort(key=lambda d: d.get("score", 0), reverse=True)
        merged_hits = merged_hits[:max_results]

        docs = await _process_chunk_hits(merged_hits, include_original_score=False)

        return SearchResult(question=query, docs=docs)

    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Semantic search failed: {exc}. Try simplifying the query or retrying."
        ) from exc


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def hybrid_search(
    query: Annotated[
        str | list[str],
        Field(
            description="Search terms including CVE IDs, package names, "
            "commands, or keywords. Use a list for multiple keyword "
            "variations. Examples: 'CVE-2023-46604' or "
            "['CVE-2023-46604', 'ActiveMQ vulnerability']"
        ),
    ],
    max_results: Annotated[
        int,
        Field(
            description="Number of documents to return. Use 5 for focused "
            "queries, 10+ for broad research.",
            ge=1,
            le=20,
        ),
    ] = 5,
) -> SearchResult:
    """Search Red Hat documentation using keyword matching with semantic reranking.

    Combines keyword/term matching with semantic understanding. Results are
    first matched by keywords, then reranked by semantic similarity.

    USE THIS WHEN:
        - Query contains specific identifiers (CVE-2024-1234, httpd, cockpit)
        - Looking for exact commands or package names (dnf, systemctl, getsebool)
        - Searching for error messages or log snippets
        - Query mixes specific terms with context ("install cockpit RHEL 10")

    USE semantic_search INSTEAD WHEN:
        - Asking pure conceptual "how do I..." questions
        - Query is entirely natural language without specific terms
        - Looking for general explanations or overviews

    Examples:
        - "CVE-2023-46604" (specific CVE lookup)
        - "getsebool semanage boolean RHEL" (command-focused)
        - "dnf install cockpit RHEL 10" (package installation)
        - "firewalld port 8080 zone" (specific configuration)
        - "sshd_config PermitRootLogin" (config file options)

    MULTI-QUERY SUPPORT:
        Pass multiple keyword variations to improve recall. Results are merged
        with max-score deduplication.

        Example - single query:
            hybrid_search(query="CVE-2023-46604")

        Example - multiple query variations:
            hybrid_search(query=[
                "CVE-2023-46604",
                "ActiveMQ vulnerability CVE",
                "Apache ActiveMQ remote code execution"
            ])

    Query tips:
        - Include relevant command names (systemctl, dnf, semanage)
        - Add RHEL version for version-specific docs ("RHEL 10", "RHEL 9")
        - Combine keywords for precision, but keep it readable
        - Don't over-stuff keywords; 3-6 relevant terms is usually optimal
        - For difficult matches, try 2-3 keyword variations

    Returns:
        SearchResult containing:
            - question: The original query (or list of queries)
            - docs: List of Document objects, each with:
                - title: Document title
                - reference_url: Link to official Red Hat docs
                - text: Relevant excerpt (context-expanded around match)
                - score: Relevance score (higher = more relevant, includes rerank weight)
                - original_score: Pre-rerank keyword match score
                - chunks: Individual text chunks that matched
    """
    if embedding_model is None:
        raise ToolError(
            "Embedding model not initialized — server may still be starting up. Retry in a few seconds."
        )

    queries = [query] if isinstance(query, str) else query

    try:
        all_chunk_hits: dict[tuple[str, int], dict] = {}

        for q in queries:
            vector = embedding_model.encode(q)
            async with SolrClient() as client:
                chunk_hits = await client.hybrid_search(q, vector, rows=max_results)

            for hit in chunk_hits:
                key = (hit["parent_id"], hit["chunk_index"])
                if (
                    key not in all_chunk_hits
                    or hit["score"] > all_chunk_hits[key]["score"]
                ):
                    all_chunk_hits[key] = hit

        merged_hits = list(all_chunk_hits.values())
        merged_hits.sort(key=lambda d: d.get("score", 0), reverse=True)
        merged_hits = merged_hits[:max_results]

        docs = await _process_chunk_hits(merged_hits, include_original_score=True)

        return SearchResult(question=query, docs=docs)

    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Hybrid search failed: {exc}. Try simplifying the query or retrying."
        ) from exc


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def explore_collection() -> CollectionInfo:
    """Explore the Red Hat documentation collection to understand what's available.

    Use this tool FIRST when you need to understand the scope and content of the
    knowledge base before searching. Returns collection statistics and sample
    documents to help you formulate effective search queries.

    WHEN TO USE:
        - Before your first search, to understand what content is available
        - When you're unsure what types of documents exist (CVEs, errata, docs)
        - To get a sense of collection size and document distribution
        - When search results are poor and you need to understand the data better

    NO PARAMETERS REQUIRED - just call the tool to explore.

    Returns:
        CollectionInfo containing:
            - total_documents: Total count of all documents and chunks
            - parent_documents: Count of unique documents (not chunks)
            - chunks: Count of text chunks (searchable segments)
            - document_kinds: Breakdown by document type with counts
            - sample_documents: 5 example documents showing typical content

    Document kinds and recommended search strategies:
        - 'errata' (RHSA/RHBA/RHEA advisories): Use hybrid_search with the
          advisory ID (e.g., 'RHSA-2024:1234') or package name
        - 'cve' (security vulnerabilities): Use hybrid_search with the CVE ID
          (e.g., 'CVE-2024-1234') or affected component name
        - 'documentation' (official RHEL docs): Use semantic_search with natural
          language questions about configuration, administration, or concepts
        - 'unknown' (other content): Try both search tools

    After exploring, use semantic_search for conceptual questions or
    hybrid_search for specific identifiers (CVE IDs, package names, commands).
    """
    async with SolrClient() as client:
        stats = await client.get_collection_stats()
        samples = await client.get_sample_documents(limit=5)

    sample_docs = [
        SampleDocument(
            doc_id=s["doc_id"],
            title=s["title"],
            reference_url=s.get("reference_url"),
            total_chunks=s.get("total_chunks"),
            total_tokens=s.get("total_tokens"),
        )
        for s in samples
    ]

    return CollectionInfo(
        total_documents=stats["total_documents"],
        parent_documents=stats["parent_documents"],
        chunks=stats["chunks"],
        document_kinds=stats["document_kinds"],
        sample_documents=sample_docs,
    )


@mcp.prompt()
def investigate_cve(cve_id: str) -> str:
    """Investigate a specific CVE across RHEL documentation and errata."""
    return (
        f"Search for all information about {cve_id}:\n"
        f"1. Use hybrid_search('{cve_id}') to find the CVE advisory and errata\n"
        f"2. Use semantic_search('impact and mitigation of {cve_id}') for broader context\n"
        "3. Summarize: severity, affected packages, fix availability, and mitigation steps\n"
        "4. Include reference_url links for all sources"
    )


@mcp.prompt()
def troubleshoot_package(package_name: str) -> str:
    """Research a RHEL package across documentation, errata, and CVEs."""
    return (
        f"Research the package '{package_name}' across RHEL documentation:\n"
        f"1. Use hybrid_search('{package_name}') to find errata, updates, and known issues\n"
        f"2. Use semantic_search('how to configure {package_name} on RHEL') for setup guidance\n"
        "3. Summarize: latest updates, known issues, configuration guidance\n"
        "4. Include reference_url links for all sources"
    )


@mcp.prompt()
def diagnose_error(error_message: str) -> str:
    """Diagnose an error message using RHEL documentation and advisories."""
    return (
        f"Diagnose this error using RHEL documentation:\n"
        f"Error: {error_message}\n\n"
        f"1. Use hybrid_search('{error_message}') to find exact matches in docs and errata\n"
        f"2. Use semantic_search('troubleshoot {error_message}') for related guidance\n"
        "3. Summarize: likely cause, resolution steps, and any related errata or CVEs\n"
        "4. Include reference_url links for all sources"
    )


def main():
    """Entry point for okp-mcp command."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="OKP MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8000")),
        help="Port to bind to (default: 8000)",
    )

    args = parser.parse_args()
    mcp.run(transport=args.transport, host=args.host, port=args.port)
