"""Unit tests for the portal search query builders and intent detection."""

import pytest

from okp_mcp.portal import (
    _EOL_PRODUCTS,
    _EUS_HIGHLIGHT_TERMS,
    _MAIN_QF,
    _VM_HIGHLIGHT_TERMS,
    _apply_intent_boosts,
    _build_deprecation_query,
    _build_eol_filter,
    _build_main_query,
    _detect_eus_intent,
    _detect_release_date_intent,
    _detect_vm_intent,
)

# ---------------------------------------------------------------------------
# EOL products
# ---------------------------------------------------------------------------


class TestEolProducts:
    """Verify the EOL product set and filter builder."""

    def test_eol_products_is_frozenset(self):
        """_EOL_PRODUCTS is an immutable frozenset."""
        assert isinstance(_EOL_PRODUCTS, frozenset)

    def test_eol_products_contains_known_entries(self):
        """Spot-check that well-known EOL products are present."""
        assert "Red Hat Virtualization" in _EOL_PRODUCTS
        assert "Red Hat Software Collections" in _EOL_PRODUCTS
        assert "Red Hat Decision Manager" in _EOL_PRODUCTS

    def test_eol_products_excludes_active(self):
        """Active products should not appear in the EOL set."""
        assert "Red Hat Enterprise Linux" not in _EOL_PRODUCTS
        assert "Red Hat OpenShift Container Platform" not in _EOL_PRODUCTS

    def test_build_eol_filter_excludes_all_products(self):
        """_build_eol_filter produces an AND-joined fq excluding every EOL product."""
        fq = _build_eol_filter()
        for product in _EOL_PRODUCTS:
            assert f'-product:"{product}"' in fq

    def test_build_eol_filter_uses_and_conjunction(self):
        """Filter clauses are joined with AND so Solr applies all exclusions."""
        fq = _build_eol_filter()
        # With 25 products there should be 24 AND separators
        assert fq.count(" AND ") == len(_EOL_PRODUCTS) - 1


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectVmIntent:
    """Verify VM/virtualization intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "create a vm on RHEL 9",
            "virtual machine performance tuning",
            "virtualization best practices",
            "running vms on rhel",
            "hypervisor configuration",
        ],
        ids=["vm", "virtual-machine", "virtualization", "vms", "hypervisor"],
    )
    def test_positive(self, query: str):
        """Queries containing VM keywords trigger VM intent."""
        assert _detect_vm_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "configure firewall rhel 9",
            "kernel tuning",
            "",
        ],
        ids=["firewall", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries without VM keywords do not trigger VM intent."""
        assert _detect_vm_intent(query) is False


class TestDetectReleaseDateIntent:
    """Verify release-date intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "rhel 9 release date",
            "when was rhel 8 released",
            "general availability of rhel 10",
        ],
        ids=["release-date", "when-was-released", "general-availability"],
    )
    def test_positive(self, query: str):
        """Queries about release timing trigger release-date intent."""
        assert _detect_release_date_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "install rhel 9",
            "kernel updates",
            "",
        ],
        ids=["install", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries not about release dates do not trigger the intent."""
        assert _detect_release_date_intent(query) is False


class TestDetectEusIntent:
    """Verify EUS (Extended Update Support) intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "what is eus support",
            "rhel 9 eus lifecycle",
            "extended update support policy",
        ],
        ids=["eus-keyword", "eus-lifecycle", "extended-update-support"],
    )
    def test_positive(self, query: str):
        """Queries mentioning EUS or Extended Update Support trigger EUS intent."""
        assert _detect_eus_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "configure firewall",
            "kernel updates rhel 9",
            "",
        ],
        ids=["firewall", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries without EUS keywords do not trigger EUS intent."""
        assert _detect_eus_intent(query) is False


# ---------------------------------------------------------------------------
# Main query builder
# ---------------------------------------------------------------------------


class TestBuildMainQuery:
    """Verify the unified main query parameter builder."""

    def test_query_is_set(self):
        """The cleaned query text appears as the Solr q parameter."""
        params = _build_main_query("kernel panic rhel 9")
        assert params["q"] == "kernel panic rhel 9"

    def test_no_document_kind_filter(self):
        """Main query must NOT restrict documentKind so all types are searched."""
        params = _build_main_query("test query")
        fq = params["fq"]
        # fq is a string (EOL filter only), not a list with documentKind
        assert isinstance(fq, str)
        assert "documentKind" not in fq

    def test_custom_qf_includes_portal_synopsis(self):
        """Custom qf adds portal_synopsis boost for errata scoring."""
        params = _build_main_query("test")
        assert params["qf"] == _MAIN_QF
        assert "portal_synopsis^3" in params["qf"]

    def test_custom_qf_excludes_cve_details(self):
        """cve_details is a string field and must not appear in qf."""
        params = _build_main_query("test")
        assert "cve_details" not in params["qf"]

    def test_fl_includes_type_specific_fields(self):
        """fl includes CVE and errata fields for chunk-conversion fallback."""
        params = _build_main_query("test")
        fl = params["fl"]
        assert "cve_details" in fl
        assert "cve_threatSeverity" in fl
        assert "portal_synopsis" in fl
        assert "portal_advisory_type" in fl

    def test_rows_overfetch(self):
        """rows=20 over-fetches for diversity after parent deduplication."""
        params = _build_main_query("test")
        assert params["rows"] == 20

    def test_hl_default_summary_false(self):
        """defaultSummary is disabled so CVE/errata boilerplate triggers fallback."""
        params = _build_main_query("test")
        assert params["hl.defaultSummary"] == "false"

    def test_recency_boost(self):
        """A recency bf boost is applied to favor newer content."""
        params = _build_main_query("test")
        assert "bf" in params
        assert "recip(ms(NOW,lastModifiedDate)" in params["bf"]

    def test_eol_filter_applied(self):
        """EOL products are excluded via fq."""
        params = _build_main_query("test")
        for product in ["Red Hat Virtualization", "Red Hat Software Collections"]:
            assert f'-product:"{product}"' in params["fq"]

    def test_highlight_snippets(self):
        """6 highlight snippets requested per document."""
        params = _build_main_query("test")
        assert params["hl.snippets"] == "6"


# ---------------------------------------------------------------------------
# Deprecation query builder
# ---------------------------------------------------------------------------


class TestBuildDeprecationQuery:
    """Verify the deprecation-focused query parameter builder."""

    def test_query_appends_deprecation_terms(self):
        """'deprecated removed' is appended to the user query."""
        params = _build_deprecation_query("virt-manager rhel 9")
        assert params["q"] == "virt-manager rhel 9 deprecated removed"

    def test_restricted_to_docs_solutions_articles(self):
        """Only docs, solutions, and articles are searched (no CVEs/errata)."""
        params = _build_deprecation_query("test")
        fq_list = params["fq"]
        assert isinstance(fq_list, list)
        kind_fq = fq_list[0]
        assert "documentKind:(solution OR article OR documentation)" in kind_fq

    def test_eol_filter_applied(self):
        """EOL products are excluded from the deprecation query too."""
        params = _build_deprecation_query("test")
        fq_list = params["fq"]
        eol_fq = fq_list[1]
        assert '-product:"Red Hat Virtualization"' in eol_fq

    def test_deprecation_bq_boosts(self):
        """bq heavily boosts deprecation-related title and content terms."""
        params = _build_deprecation_query("test")
        bq = params["bq"]
        assert "deprecated" in bq
        assert "removed" in bq
        assert "release notes" in bq
        assert "considerations in adopting" in bq

    def test_fewer_rows_than_main(self):
        """Deprecation query fetches fewer rows (5) than the main query (20)."""
        params = _build_deprecation_query("test")
        assert params["rows"] == 5

    def test_fewer_highlight_snippets(self):
        """4 highlight snippets per doc (vs 6 in main query)."""
        params = _build_deprecation_query("test")
        assert params["hl.snippets"] == "4"

    def test_fl_excludes_cve_fields(self):
        """Deprecation fl does not include CVE/errata-specific fields."""
        params = _build_deprecation_query("test")
        fl = params["fl"]
        assert "cve_details" not in fl
        assert "portal_synopsis" not in fl


# ---------------------------------------------------------------------------
# Intent boost application
# ---------------------------------------------------------------------------


class TestApplyIntentBoosts:
    """Verify that _apply_intent_boosts mutates params correctly for each intent."""

    def test_vm_intent_adds_bq_and_hlq(self):
        """VM intent injects cockpit/virt-manager bq and expands hl.q."""
        params = _build_main_query("create a vm")
        _apply_intent_boosts(params, "create a vm", "create vm")
        assert "cockpit" in params["bq"]
        assert "virt-manager" in params["bq"]
        assert _VM_HIGHLIGHT_TERMS in params["hl.q"]
        assert params["hl.q"].startswith("create vm")

    def test_eus_intent_adds_bq_and_hlq(self):
        """EUS intent injects Enhanced EUS bq and expands hl.q."""
        params = _build_main_query("eus support")
        _apply_intent_boosts(params, "eus support", "eus support")
        assert "Enhanced EUS" in params["bq"]
        assert "EUS FAQ" in params["bq"]
        assert _EUS_HIGHLIGHT_TERMS in params["hl.q"]

    def test_release_date_intent_adds_bq(self):
        """Release-date intent injects release dates bq."""
        params = _build_main_query("when was rhel 9 released")
        _apply_intent_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "Enterprise Linux Release Dates" in params["bq"]
        assert 'allTitle:"release dates"' in params["bq"]

    def test_release_date_intent_no_hlq(self):
        """Release-date intent does not override hl.q (no special highlight terms)."""
        params = _build_main_query("when was rhel 9 released")
        _apply_intent_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "hl.q" not in params

    def test_no_intent_leaves_params_unchanged(self):
        """Params are not mutated when no intent is detected."""
        params = _build_main_query("configure firewall rhel 9")
        original = dict(params)
        _apply_intent_boosts(params, "configure firewall rhel 9", "configure firewall rhel 9")
        assert params == original

    @pytest.mark.parametrize(
        "query",
        ["nvme tuning rhel 9", "jvm heap configuration", "evms partition"],
        ids=["nvme", "jvm", "evms"],
    )
    def test_vm_intent_no_false_positive(self, query):
        """Substrings containing 'vm' (nvme, jvm, evms) must not trigger VM intent."""
        assert not _detect_vm_intent(query)

    def test_eus_intent_no_false_positive(self):
        """Substrings containing 'eus' (e.g. 'zeus') must not trigger EUS intent."""
        assert not _detect_eus_intent("zeus cluster setup")

    def test_release_date_intent_no_false_positive(self):
        """Substrings containing 'released' fragment must not trigger release-date intent."""
        assert not _detect_release_date_intent("unreleased feature flag")

    def test_eus_overrides_vm_when_both_match(self):
        """When both VM and EUS match, EUS runs second and overwrites bq/hl.q."""
        # "eus" is a substring that doesn't match VM intent, but test the
        # override behavior when both would match (e.g., "virtualization eus").
        params = _build_main_query("virtualization eus")
        _apply_intent_boosts(params, "virtualization eus", "virtualization eus")
        # EUS runs after VM, so it wins
        assert "Enhanced EUS" in params["bq"]
        assert _EUS_HIGHLIGHT_TERMS in params["hl.q"]

    def test_mutates_in_place(self):
        """_apply_intent_boosts modifies the dict in-place, returns None."""
        params = _build_main_query("eus policy")
        result = _apply_intent_boosts(params, "eus policy", "eus policy")
        assert result is None
        assert "bq" in params
