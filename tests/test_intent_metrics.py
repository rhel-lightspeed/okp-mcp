"""Tests for intent detection Prometheus metrics instrumentation."""

from prometheus_client import REGISTRY

from okp_mcp.intent import apply_deprecation_boosts
from okp_mcp.intent import apply_main_boosts


def _get_counter(name: str, labels: dict) -> float:
    """Read the current value of a Prometheus counter, defaulting to 0."""
    return REGISTRY.get_sample_value(f"{name}_total", labels) or 0.0


# ---------------------------------------------------------------------------
# apply_main_boosts metrics
# ---------------------------------------------------------------------------


def test_main_boost_match_increments_intent_matched():
    """Matching a rule in the main path increments okp_intent_matched_total."""
    labels = {"intent": "spice", "query_path": "main"}
    before = _get_counter("okp_intent_matched", labels)

    params: dict = {}
    apply_main_boosts(params, "how to use spice on rhel", "spice rhel")

    assert _get_counter("okp_intent_matched", labels) == before + 1


def test_main_boost_no_match_increments_no_match():
    """Exhausting all rules without a match increments okp_intent_no_match_total."""
    labels = {"query_path": "main"}
    before = _get_counter("okp_intent_no_match", labels)

    params: dict = {}
    apply_main_boosts(params, "completely unrelated topic xyz", "unrelated topic xyz")

    assert _get_counter("okp_intent_no_match", labels) == before + 1


def test_main_boost_match_does_not_increment_no_match():
    """A successful match must not also increment the no-match counter."""
    no_match_labels = {"query_path": "main"}
    before_no_match = _get_counter("okp_intent_no_match", no_match_labels)

    params: dict = {}
    apply_main_boosts(params, "eus support policy", "eus support policy")

    assert _get_counter("okp_intent_no_match", no_match_labels) == before_no_match


# ---------------------------------------------------------------------------
# apply_deprecation_boosts metrics
# ---------------------------------------------------------------------------


def test_deprecation_boost_match_increments_intent_matched():
    """Matching a rule with dep_title_terms increments okp_intent_matched_total for deprecation."""
    labels = {"intent": "spice", "query_path": "deprecation"}
    before = _get_counter("okp_intent_matched", labels)

    params: dict = {}
    apply_deprecation_boosts(params, "is spice deprecated in rhel 10")

    assert _get_counter("okp_intent_matched", labels) == before + 1


def test_deprecation_boost_skipped_increments_skipped_counter():
    """Rule match with empty dep_title_terms increments okp_intent_deprecation_skipped_total."""
    labels = {"intent": "release_date"}
    before = _get_counter("okp_intent_deprecation_skipped", labels)

    params: dict = {}
    apply_deprecation_boosts(params, "when was rhel 10 released")

    assert _get_counter("okp_intent_deprecation_skipped", labels) == before + 1


def test_deprecation_boost_no_match_increments_no_match():
    """Exhausting all rules without a match increments okp_intent_no_match_total for deprecation."""
    labels = {"query_path": "deprecation"}
    before = _get_counter("okp_intent_no_match", labels)

    params: dict = {}
    apply_deprecation_boosts(params, "completely unrelated topic xyz")

    assert _get_counter("okp_intent_no_match", labels) == before + 1


def test_deprecation_skipped_does_not_increment_matched_or_no_match():
    """A skipped deprecation must not also increment the matched or no-match counters."""
    matched_labels = {"intent": "release_date", "query_path": "deprecation"}
    no_match_labels = {"query_path": "deprecation"}
    before_matched = _get_counter("okp_intent_matched", matched_labels)
    before_no_match = _get_counter("okp_intent_no_match", no_match_labels)

    params: dict = {}
    apply_deprecation_boosts(params, "when was rhel 10 released")

    assert _get_counter("okp_intent_matched", matched_labels) == before_matched
    assert _get_counter("okp_intent_no_match", no_match_labels) == before_no_match
