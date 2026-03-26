"""Tests for okp_mcp.formatting module."""

import pytest

from okp_mcp.formatting import (
    SORT_DEPRECATION,
    SORT_EOL_PRODUCT,
    SORT_REPLACEMENT,
    _annotate_result,
    _format_result,
)


@pytest.mark.parametrize(
    "main_content,max_content,expect_truncated",
    [
        ("x" * 10_000, 200, True),
        ("Brief content about kernels.", 5000, False),
    ],
    ids=["large-content-truncated", "short-content-preserved"],
)
async def test_format_result_content_cap(main_content: str, max_content: int, expect_truncated: bool):
    """_format_result caps content at max_content, appending [...] when truncated."""
    doc = {
        "id": "doc-1",
        "allTitle": "Test Doc",
        "documentKind": "solution",
        "view_uri": "/test-doc",
        "main_content": main_content,
    }
    data: dict = {"highlighting": {}}
    result, _ = await _format_result(doc, data, include_content=True, query="test", max_content=max_content)

    if expect_truncated:
        assert "[...]" in result
        content_start = result.index("Content: ") + len("Content: ")
        assert len(result[content_start:]) < max_content + 100
    else:
        assert "[...]" not in result
        assert main_content in result


# ---------------------------------------------------------------------------
# EOL false-positive demotion regression tests
# ---------------------------------------------------------------------------


class TestEolProductFieldGuard:
    """Verify that docs with a non-EOL product field are never demoted as EOL content."""

    def test_non_eol_product_mentioning_rhv_not_demoted(self):
        """Doc with product='Red Hat OpenShift Data Foundation' mentioning RHV is NOT demoted."""
        _, _, sort_key = _annotate_result(
            title="Troubleshooting OpenShift Data Foundation",
            highlights="Deploy on Red Hat Virtualization or VMware",
            content="Configure storage on Red Hat Virtualization hosts",
            product="Red Hat OpenShift Data Foundation",
        )
        assert sort_key != SORT_EOL_PRODUCT

    def test_non_eol_product_mentioning_fuse_not_demoted(self):
        """Doc with product='Red Hat build of Apache Camel' mentioning Fuse is NOT demoted."""
        _, _, sort_key = _annotate_result(
            title="Support of Red Hat Middleware on OpenShift",
            highlights="Red Hat Fuse is available as a container image",
            content="Middleware products including Red Hat Fuse",
            product="Red Hat build of Apache Camel",
        )
        assert sort_key != SORT_EOL_PRODUCT

    def test_empty_product_mentioning_rhv_still_demoted(self):
        """Doc with no product field (solutions/articles) mentioning RHV IS still demoted."""
        _, _, sort_key = _annotate_result(
            title="How to configure virtual machines",
            highlights="Use Red Hat Virtualization Manager to create VMs",
            content="Red Hat Virtualization provides centralized management",
        )
        assert sort_key == SORT_EOL_PRODUCT

    def test_eol_product_field_still_demoted(self):
        """Doc whose own product IS an EOL product is correctly demoted."""
        _, _, sort_key = _annotate_result(
            title="Installing Red Hat Virtualization Manager",
            highlights="Red Hat Virtualization Manager installation guide",
            content="Red Hat Virtualization 4.4 installation",
            product="Red Hat Virtualization",
        )
        assert sort_key == SORT_EOL_PRODUCT

    def test_eol_product_gluster_in_product_field_demoted(self):
        """Doc with product='Red Hat Gluster Storage' is correctly demoted."""
        _, _, sort_key = _annotate_result(
            title="Red Hat Gluster Storage Administration",
            highlights="Gluster volume configuration",
            content="Red Hat Gluster Storage cluster setup",
            product="Red Hat Gluster Storage",
        )
        assert sort_key == SORT_EOL_PRODUCT

    def test_no_product_param_defaults_to_empty(self):
        """Calling without product param preserves existing behavior (backward compat)."""
        _, _, sort_key = _annotate_result(
            title="Some doc",
            highlights="Mentions Red Hat Fuse in passing",
            content="",
        )
        assert sort_key == SORT_EOL_PRODUCT

    def test_deprecation_annotation_preserved_with_product_guard(self):
        """Deprecation annotations still work when product guard skips EOL scan."""
        annotations, _, sort_key = _annotate_result(
            title="Feature deprecated in RHEL 9",
            highlights="This feature has been deprecated and Red Hat Virtualization mentioned",
            content="deprecated in favor of new approach",
            product="Red Hat Enterprise Linux",
        )
        assert sort_key == SORT_DEPRECATION
        assert any("Deprecation" in a for a in annotations)

    def test_replacement_annotation_preserved_with_product_guard(self):
        """Replacement annotations still work when product guard skips EOL scan."""
        annotations, _, sort_key = _annotate_result(
            title="Cockpit replaces old admin tool",
            highlights="replaced by cockpit, Red Hat Virtualization is mentioned",
            content="the recommended replacement is cockpit",
            product="Red Hat Enterprise Linux",
        )
        assert sort_key == SORT_REPLACEMENT
        assert any("replacement" in a.lower() for a in annotations)


class TestFormatResultProductPassthrough:
    """Verify that _format_result passes the doc product field through to _annotate_result."""

    async def test_format_result_non_eol_product_not_demoted(self):
        """_format_result does not demote docs with a non-EOL product that mention EOL products."""
        doc = {
            "id": "doc-odf",
            "allTitle": "Troubleshooting OpenShift Data Foundation",
            "documentKind": "documentation",
            "view_uri": "/docs/odf",
            "product": "Red Hat OpenShift Data Foundation",
            "main_content": "Deploy on Red Hat Virtualization or VMware for storage.",
        }
        data: dict = {"highlighting": {}}
        _, sort_key = await _format_result(doc, data, include_content=True, query="odf troubleshoot")
        assert sort_key != SORT_EOL_PRODUCT

    async def test_format_result_eol_product_still_demoted(self):
        """_format_result still demotes docs whose product IS an EOL product."""
        doc = {
            "id": "doc-rhv",
            "allTitle": "Red Hat Virtualization Installation Guide",
            "documentKind": "documentation",
            "view_uri": "/docs/rhv",
            "product": "Red Hat Virtualization",
            "main_content": "Red Hat Virtualization Manager installation steps.",
        }
        data: dict = {"highlighting": {}}
        _, sort_key = await _format_result(doc, data, include_content=True, query="install rhv")
        assert sort_key == SORT_EOL_PRODUCT
