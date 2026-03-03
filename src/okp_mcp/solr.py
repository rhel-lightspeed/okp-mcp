"""Async Solr client for OKP RAG queries."""

import os

import httpx

# Constants from okp_query.py
SOLR_HOST = os.environ.get("SOLR_HOST", "127.0.0.1:8080")
SOLR_COLLECTION = "portal-rag"


class SolrClient:
    """Async HTTP client for Solr RAG operations."""

    def __init__(self, host: str = SOLR_HOST, collection: str = SOLR_COLLECTION):
        """Initialize Solr client.

        Args:
            host: Solr host:port (default: 127.0.0.1:8080)
            collection: Solr collection name (default: portal-rag)
        """
        self.base_url = f"http://{host}"
        self.collection = collection
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        """Enter async context manager."""
        self._client = httpx.AsyncClient(base_url=self.base_url)
        return self

    async def __aexit__(self, *args):
        """Exit async context manager."""
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make HTTP request to Solr.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path
            **kwargs: Additional arguments for httpx request

        Returns:
            JSON response as dict

        Raises:
            RuntimeError: If client not initialized (use async context manager)
            httpx.HTTPStatusError: If response status is 4xx or 5xx
        """
        if self._client is None:
            raise RuntimeError("SolrClient must be used as async context manager")
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def semantic_search(self, vector: list[float], rows: int = 5) -> list[dict]:
        """Perform semantic search using KNN.

        Pattern from okp_query.py:105-120

        Args:
            vector: Embedding vector for query
            rows: Number of results to return (default: 5)

        Returns:
            List of matching chunk documents
        """
        knn_query = f"{{!knn f=chunk_vector topK=10}}{vector}"
        payload = {
            "params": {
                "q": knn_query,
                "rows": str(rows),
                "fl": "doc_id,parent_id,chunk_index,chunk,num_tokens,score",
            }
        }
        result = await self._request(
            "POST",
            f"/solr/{self.collection}/semantic-search",
            headers={"Content-Type": "application/json"},
            params={"wt": "json"},
            json=payload,
        )
        return result["response"]["docs"]

    async def hybrid_search(
        self, question: str, vector: list[float], rows: int = 5
    ) -> list[dict]:
        """Perform hybrid search with keyword + semantic reranking.

        Pattern from okp_query.py:244-262

        Args:
            question: Query text for keyword search
            vector: Embedding vector for semantic reranking
            rows: Number of results to return (default: 5)

        Returns:
            List of matching chunk documents with reranked scores
        """
        knn_query = f"{{!vectorSimilarity f=chunk_vector minReturn=0.7}}{vector}"
        payload = {
            "params": {
                "q": question,
                "rq": "{!rerank reRankQuery=$rqq reRankDocs=50 reRankWeight=5 reRankOperator=multiply}",
                "rqq": knn_query,
                "rows": str(rows),
                "fl": "doc_id,parent_id,chunk_index,chunk,num_tokens,score,originalScore()",
                "fq": "is_chunk:true",
            }
        }
        result = await self._request(
            "POST",
            f"/solr/{self.collection}/hybrid-search",
            headers={"Content-Type": "application/json"},
            params={"wt": "json"},
            json=payload,
        )
        return result["response"]["docs"]

    async def get_parent_metadata(self, parent_id: str) -> dict:
        """Get parent document metadata.

        Pattern from okp_query.py:149-158

        Args:
            parent_id: Parent document ID

        Returns:
            Parent document metadata dict

        Raises:
            IndexError: If parent document not found
        """
        result = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={
                "q": f'id:"{parent_id}"',
                "fl": "doc_id,title,end_chunk_index,total_chunks,total_tokens,reference_url",
                "wt": "json",
            },
        )
        return result["response"]["docs"][0]

    async def get_chunks(
        self, parent_id: str, start: int | None = None, end: int | None = None
    ) -> list[dict]:
        """Get chunks for a parent document.

        Pattern from okp_query.py:163-173, 183-193

        Args:
            parent_id: Parent document ID
            start: Start chunk index (optional)
            end: End chunk index (optional)

        Returns:
            List of chunk documents sorted by chunk_index
        """
        if start is not None and end is not None:
            q = f'parent_id:"{parent_id}" AND chunk_index:[{start} TO {end}]'
        else:
            q = f'parent_id:"{parent_id}"'

        result = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={
                "q": q,
                "rows": "100",
                "sort": "chunk_index asc",
                "fl": "chunk_index,chunk,num_tokens",
                "wt": "json",
            },
        )
        return result["response"]["docs"]

    async def get_collection_stats(self) -> dict:
        """Get collection statistics and document counts.

        Returns:
            Dict with total docs, parent docs, chunks, and document kind breakdown
        """
        total = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={"q": "*:*", "rows": "0", "wt": "json"},
        )

        parents = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={"q": "is_chunk:false", "rows": "0", "wt": "json"},
        )

        chunks = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={"q": "is_chunk:true", "rows": "0", "wt": "json"},
        )

        doc_kind_patterns = {
            "errata": "is_chunk:false AND doc_id:*errata*",
            "cve": "is_chunk:false AND doc_id:*security/cve*",
            "documentation": "is_chunk:false AND doc_id:*documentation*",
        }

        doc_kinds: dict[str, int] = {}
        for kind, query in doc_kind_patterns.items():
            result = await self._request(
                "GET",
                f"/solr/{self.collection}/select",
                params={"q": query, "rows": "0", "wt": "json"},
            )
            doc_kinds[kind] = result["response"]["numFound"]

        known_count = sum(doc_kinds.values())
        unknown_count = parents["response"]["numFound"] - known_count
        if unknown_count > 0:
            doc_kinds["unknown"] = unknown_count

        return {
            "total_documents": total["response"]["numFound"],
            "parent_documents": parents["response"]["numFound"],
            "chunks": chunks["response"]["numFound"],
            "document_kinds": doc_kinds,
        }

    async def get_sample_documents(
        self, doc_type: str | None = None, limit: int = 5
    ) -> list[dict]:
        """Get sample documents from the collection.

        Args:
            doc_type: Filter by document type pattern (e.g., 'errata', 'cve', 'documentation')
            limit: Number of samples to return

        Returns:
            List of sample parent documents with key metadata
        """
        if doc_type:
            q = f"is_chunk:false AND doc_id:*{doc_type}*"
        else:
            q = "is_chunk:false"

        result = await self._request(
            "GET",
            f"/solr/{self.collection}/select",
            params={
                "q": q,
                "rows": str(limit),
                "wt": "json",
                "fl": "doc_id,title,reference_url,total_chunks,total_tokens,documentKind",
            },
        )
        return result["response"]["docs"]
