"""Pydantic response models for portal-rag and portal Solr core query results."""

from pydantic import BaseModel, ConfigDict

# --- portal-rag core models (chunked documents with vector embeddings) ---


class RagDocument(BaseModel):
    """A single document chunk returned from the portal-rag Solr core.

    Each chunk has a parent_id linking back to its source document, a chunk
    field with passage-sized text, vector embedding metadata (chunk_index,
    num_tokens), and online_source_url as a full URL. This differs from
    PortalDocument, which represents flat whole documents from the legacy
    portal core with main_content as the full body, url_slug for linking,
    and no parent-child chunking.
    """

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


# --- portal core models (flat whole documents, solutions and articles) ---


class PortalDocument(BaseModel):
    """A single flat document returned from the portal Solr core.

    Unlike RagDocument (which represents chunked RAG documents from portal-rag),
    PortalDocument maps to whole documents from the legacy portal core, primarily
    solutions and articles that are missing from the portal-rag core.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    resourceName: str | None = None
    title: str | None = None
    main_content: str | None = None
    url_slug: str | None = None
    documentKind: str | None = None
    heading_h1: list[str] | None = None
    heading_h2: list[str] | None = None
    lastModifiedDate: str | None = None
    score: float | None = None


class PortalResponse(BaseModel):
    """Parsed response from the portal Solr core."""

    num_found: int
    docs: list[PortalDocument]
