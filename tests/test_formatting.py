"""Tests for okp_mcp.formatting module."""

from okp_mcp.formatting import (
    SORT_DEPRECATION,
    SORT_EOL_PRODUCT,
    SORT_REPLACEMENT,
    _annotate_result,
)

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
