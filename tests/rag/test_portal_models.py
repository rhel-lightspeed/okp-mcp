"""Unit tests for PortalDocument and PortalResponse Pydantic models."""

import pytest
from pydantic import ValidationError

from okp_mcp.rag.models import PortalDocument, PortalResponse


def test_portal_document_extra_field_storage():
    """PortalDocument stores extra fields via extra='allow'."""
    doc = PortalDocument(id="x", unknown_field="y")
    assert doc.id == "x"
    assert doc.unknown_field == "y"  # type: ignore[attr-defined]


def test_portal_document_equality_with_extra_fields():
    """PortalDocument equality includes extra fields."""
    doc1 = PortalDocument(id="a", extra_f="v")
    doc2 = PortalDocument(id="a", extra_f="v")
    assert doc1 == doc2


def test_portal_document_all_explicit_fields():
    """PortalDocument with all explicit fields populated."""
    doc = PortalDocument(
        id="/solutions/12345/index.html",
        resourceName="/solutions/12345/index.html",
        title="How to configure SELinux on RHEL 9",
        main_content="Environment RHEL 9 Issue SELinux is...",
        url_slug="12345",
        documentKind="solution",
        heading_h1=["title"],
        heading_h2=["environment", "issue", "resolution"],
        lastModifiedDate="2024-06-14T17:18:29Z",
        score=0.95,
    )
    assert doc.id == "/solutions/12345/index.html"
    assert doc.resourceName == "/solutions/12345/index.html"
    assert doc.title == "How to configure SELinux on RHEL 9"
    assert doc.main_content == "Environment RHEL 9 Issue SELinux is..."
    assert doc.url_slug == "12345"
    assert doc.documentKind == "solution"
    assert doc.heading_h1 == ["title"]
    assert doc.heading_h2 == ["environment", "issue", "resolution"]
    assert doc.lastModifiedDate == "2024-06-14T17:18:29Z"
    assert doc.score == 0.95


@pytest.mark.parametrize(
    "field_name",
    [
        "id",
        "resourceName",
        "title",
        "main_content",
        "url_slug",
        "documentKind",
        "heading_h1",
        "heading_h2",
        "lastModifiedDate",
        "score",
    ],
)
def test_portal_document_explicit_field_defaults_to_none(field_name: str):
    """Each explicit field defaults to None when not provided."""
    assert getattr(PortalDocument(), field_name) is None


def test_portal_document_heading_h2_as_list():
    """heading_h2 field parsed as list[str]."""
    doc = PortalDocument(heading_h2=["environment", "issue", "resolution"])
    assert doc.heading_h2 == ["environment", "issue", "resolution"]
    assert isinstance(doc.heading_h2, list)


def test_portal_document_heading_h1_rejects_plain_string():
    """heading_h1 field rejects plain string (expects list)."""
    with pytest.raises(ValidationError):
        PortalDocument(heading_h1="not a list")


@pytest.mark.parametrize(
    ("solr_doc", "expected_kind", "expected_slug"),
    [
        (
            {
                "id": "/solutions/3257611/index.html",
                "documentKind": "solution",
                "url_slug": "3257611",
                "title": "usage of the service.alpha.kubernetes.io/tolerate-unready-endpoints annotation",
                "main_content": "Solution Unverified - Updated 14 Jun 2024 Environment OpenShift...",
                "heading_h2": ["environment", "issue", "resolution"],
                "lastModifiedDate": "2024-06-14T17:18:29Z",
            },
            "solution",
            "3257611",
        ),
        (
            {
                "id": "/articles/2585/index.html",
                "documentKind": "article",
                "url_slug": "2585",
                "title": "How do I debug problems in my startup scripts?",
                "main_content": "How do I debug problems in my startup scripts? Updated 16 Sept 2012...",
            },
            "article",
            "2585",
        ),
    ],
    ids=["solution", "article"],
)
def test_portal_document_realistic_solr_shape(solr_doc, expected_kind, expected_slug):
    """Construct from dict matching realistic Solr solution/article responses."""
    doc = PortalDocument(**solr_doc)
    assert doc.id == solr_doc["id"]
    assert doc.documentKind == expected_kind
    assert doc.url_slug == expected_slug


def test_portal_response_empty():
    """PortalResponse with empty docs list."""
    response = PortalResponse(num_found=0, docs=[])
    assert response.num_found == 0
    assert response.docs == []


def test_portal_response_with_documents():
    """PortalResponse with multiple documents stores and retrieves them correctly."""
    doc_a = PortalDocument(id="a", documentKind="solution")
    doc_b = PortalDocument(id="b", documentKind="article")
    response = PortalResponse(num_found=2, docs=[doc_a, doc_b])
    assert response.num_found == 2
    assert len(response.docs) == 2
    assert response.docs[0].id == "a"
    assert response.docs[1].documentKind == "article"


def test_portal_document_model_copy():
    """PortalDocument.model_copy() preserves fields and applies updates."""
    original = PortalDocument(id="orig", title="Original Title")
    copied = original.model_copy(update={"score": 0.8})
    assert copied.id == "orig"
    assert copied.title == "Original Title"
    assert copied.score == 0.8
    assert original.score is None
