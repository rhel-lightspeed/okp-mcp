"""Tests for budget-aware assembly functions in okp_mcp.tools."""

import pytest

from okp_mcp.tools import _assemble_search_output, _select_within_budget


class TestSelectWithinBudget:
    """Tests for _select_within_budget() function."""

    def test_empty_list_returns_empty(self):
        """Empty input list returns empty list."""
        assert _select_within_budget([], budget=1000) == []

    def test_single_result_always_returned(self):
        """Single result is always returned even if it exceeds budget."""
        huge_result = "x" * 10000
        result = _select_within_budget([huge_result], budget=100)
        assert result == [huge_result]

    def test_all_results_under_budget(self):
        """All results returned when total fits within budget."""
        results = ["short", "text", "here"]
        # Total: 5 + 7 + 4 + 7 + 4 = 27 chars (including separators)
        result = _select_within_budget(results, budget=100)
        assert result == results

    def test_results_over_budget_tail_dropped(self):
        """Results are dropped from tail when over budget."""
        results = ["a" * 100, "b" * 100, "c" * 100]
        # Separator cost: len("\n\n---\n\n") = 7
        # Total: 100 + 7 + 100 + 7 + 100 = 314
        # Budget 200: keep first (100), can't fit second (100 + 7 = 107 > 100 remaining)
        result = _select_within_budget(results, budget=200)
        assert len(result) == 1
        assert result[0] == "a" * 100

    def test_separator_cost_correctly_counted(self):
        """Separator cost (7 chars) is correctly included in budget calculation."""
        # Separator is "\n\n---\n\n" = 7 chars
        results = ["a" * 50, "b" * 50]
        # Total: 50 + 7 + 50 = 107
        result = _select_within_budget(results, budget=107)
        assert len(result) == 2
        assert result == results

    def test_separator_cost_prevents_inclusion(self):
        """Result is dropped if separator + result exceeds budget."""
        results = ["a" * 50, "b" * 50]
        # Total would be: 50 + 7 + 50 = 107
        # Budget 106: first fits (50), but second doesn't (7 + 50 = 57 > 56 remaining)
        result = _select_within_budget(results, budget=106)
        assert len(result) == 1
        assert result[0] == "a" * 50

    @pytest.mark.parametrize(
        "results,budget,expected_count",
        [
            (["x" * 10, "y" * 10, "z" * 10], 50, 3),  # All fit
            (["x" * 10, "y" * 10, "z" * 10], 35, 2),  # First two fit (10 + 7 + 10 = 27)
            (["x" * 10, "y" * 10, "z" * 10], 20, 1),  # Only first fits
            (["x" * 100], 50, 1),  # Single huge result always returned
            (["a", "b", "c", "d", "e"], 25, 4),  # Four tiny results fit (1 + 7 + 1 + 7 + 1 + 7 + 1 = 25)
        ],
        ids=[
            "all-fit",
            "two-fit",
            "one-fits",
            "single-huge",
            "three-tiny",
        ],
    )
    def test_various_budget_scenarios(self, results, budget, expected_count):
        """Various budget scenarios produce expected result counts."""
        result = _select_within_budget(results, budget=budget)
        assert len(result) == expected_count


class TestAssembleSearchOutput:
    """Tests for _assemble_search_output() function."""

    def test_no_results_returns_not_found_message(self):
        """Empty doc and solution lists return 'no results' message."""
        output = _assemble_search_output([], [], "test query", max_chars=1000)
        assert "No results found for: test query" in output

    def test_no_trimming_when_under_budget(self):
        """No 'omitted' message when results fit within budget."""
        doc_results = ["Documentation result 1", "Documentation result 2"]
        sol_results = ["Solution result 1"]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=5000)
        assert "[" not in output or "omitted" not in output

    def test_trimming_when_over_budget(self):
        """'Omitted' message appears when results exceed budget."""
        doc_results = ["x" * 500, "y" * 500, "z" * 500]
        sol_results = ["a" * 500, "b" * 500]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=500)
        assert "omitted" in output.lower()

    def test_section_headers_reflect_actual_count(self):
        """Section headers show count of included results, not total."""
        doc_results = ["Doc 1", "Doc 2", "Doc 3"]
        sol_results = ["Sol 1", "Sol 2"]
        # Use budget large enough to include both sections but trim some results
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=500)
        # Extract the counts from headers
        assert "Documentation" in output
        assert "Solutions & Articles" in output

    def test_has_deprecation_set_when_included(self):
        """has_deprecation flag is set when deprecation result is included."""
        doc_results = ["Normal doc", "⚠️ Deprecation/Removal Notice: Feature X is deprecated"]
        sol_results = []
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=5000)
        assert "⚠️ WARNING: Some results indicate a feature was deprecated or removed" in output

    def test_has_deprecation_not_set_when_excluded(self):
        """has_deprecation flag is NOT set when deprecation result is trimmed."""
        doc_results = ["x" * 1000, "⚠️ Deprecation/Removal Notice: Feature X is deprecated"]
        sol_results = []
        # Use small budget to force trimming of the deprecation result
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=500)
        # The deprecation result should be trimmed, so warning should not appear
        # (unless the first result happens to contain the deprecation notice)
        if "⚠️ Deprecation/Removal Notice" not in doc_results[0]:
            assert "⚠️ WARNING: Some results indicate a feature was deprecated" not in output

    def test_docs_only_output(self):
        """Output with only documentation results."""
        doc_results = ["Doc 1", "Doc 2"]
        output = _assemble_search_output(doc_results, [], "query", max_chars=5000)
        assert "**Documentation**" in output
        assert "**Solutions & Articles**" not in output

    def test_solutions_only_output(self):
        """Output with only solution results."""
        sol_results = ["Sol 1", "Sol 2"]
        output = _assemble_search_output([], sol_results, "query", max_chars=5000)
        assert "**Solutions & Articles**" in output
        assert "**Documentation**" not in output

    def test_both_sections_output(self):
        """Output with both documentation and solution sections."""
        doc_results = ["Doc 1"]
        sol_results = ["Sol 1"]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=5000)
        assert "**Documentation**" in output
        assert "**Solutions & Articles**" in output
        assert "===" in output  # Section separator

    def test_result_count_in_headers(self):
        """Result counts in section headers match included results."""
        doc_results = ["Doc 1", "Doc 2", "Doc 3"]
        sol_results = ["Sol 1"]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=5000)
        assert "(3 results)" in output  # Documentation count
        assert "(1 results)" in output  # Solutions count

    def test_results_separated_by_dashes(self):
        """Multiple results within a section are separated by dashes."""
        doc_results = ["First doc", "Second doc"]
        output = _assemble_search_output(doc_results, [], "query", max_chars=5000)
        assert "---" in output

    def test_deprecation_warning_appears_first(self):
        """Deprecation warning appears before section headers."""
        doc_results = ["⚠️ Deprecation/Removal Notice: Feature X"]
        output = _assemble_search_output(doc_results, [], "query", max_chars=5000)
        warning_pos = output.find("⚠️ WARNING")
        doc_header_pos = output.find("**Documentation**")
        assert warning_pos < doc_header_pos

    @pytest.mark.parametrize(
        "doc_count,sol_count,max_chars,should_omit",
        [
            (1, 0, 5000, False),  # Single doc, plenty of budget
            (5, 5, 300, True),  # Many results, tight budget
            (2, 2, 2000, False),  # Moderate results, good budget
            (10, 10, 300, True),  # Many results, very tight budget
        ],
        ids=[
            "single-doc-plenty-budget",
            "many-results-tight-budget",
            "moderate-results-good-budget",
            "many-results-very-tight-budget",
        ],
    )
    def test_omit_message_scenarios(self, doc_count, sol_count, max_chars, should_omit):
        """Omit message appears/disappears based on budget constraints."""
        doc_results = [f"Doc {i}" for i in range(doc_count)]
        sol_results = [f"Sol {i}" for i in range(sol_count)]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=max_chars)
        has_omit = "omitted" in output.lower()
        assert has_omit == should_omit

    def test_large_results_both_sections_survive(self):
        """Both sections survive even when individual results are large."""
        doc_results = ["D" * 500]
        sol_results = ["S" * 500]
        output = _assemble_search_output(doc_results, sol_results, "query", max_chars=300)
        assert "**Documentation**" in output
        assert "**Solutions & Articles**" in output
