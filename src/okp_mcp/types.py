"""Shared type definitions for Solr documents and responses."""

from pydantic import BaseModel
from pydantic import Field


class SolrDoc(BaseModel):
    """A Solr document (all fields optional per query fl)."""

    id: str = ""
    allTitle: str = ""
    title: str = ""
    heading_h1: list[str] = Field(default_factory=list)
    view_uri: str = ""
    url_slug: str = ""
    documentKind: str = ""
    product: str = ""
    documentation_version: str = ""
    lastModifiedDate: str = ""
    score: float | None = None
    main_content: str = ""
    cve_details: str = ""
    cve_threatSeverity: str = ""
    portal_synopsis: str = ""
    portal_summary: str = ""
    portal_severity: str = ""
    portal_advisory_type: str = ""

    model_config = {"extra": "ignore"}


class SolrResponseBody(BaseModel):
    """The nested ``response`` object inside a Solr JSON response."""

    numFound: int = 0
    docs: list[SolrDoc] = Field(default_factory=list)


class SolrResponse(BaseModel):
    """Top-level Solr JSON response structure."""

    response: SolrResponseBody = Field(default_factory=SolrResponseBody)
    highlighting: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    error: dict | None = None

    model_config = {"extra": "ignore"}
