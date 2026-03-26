"""Pydantic response models for portal-rag Solr query results."""

from pydantic import BaseModel, ConfigDict


class RagDocument(BaseModel):
    """A single document chunk returned from the portal-rag Solr core."""

    model_config = ConfigDict(extra="allow")

    doc_id: str | None = None
    parent_id: str | None = None
    title: str | None = None
    chunk: str | None = None
    rrf_score: float | None = None
    headings: str | None = None
    online_source_url: str | None = None
    product: list[str] | None = None
    product_version: str | None = None
    chunk_index: int | None = None
    num_tokens: int | None = None
    source_path: str | None = None
    documentKind: str | None = None
    score: float | None = None


class RagResponse(BaseModel):
    """Parsed response from the portal-rag Solr core."""

    num_found: int
    docs: list[RagDocument]
