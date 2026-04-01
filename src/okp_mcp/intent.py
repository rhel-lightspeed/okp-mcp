"""Intent detection and Solr boost application for portal search queries."""

# Each intent maps a regex pattern to Solr boost queries (bq) and highlight
# terms (hl.q) that improve search relevance for specific topics.  Intents
# are evaluated first-match-wins: the registry is ordered from most specific
# to most generic, and the first matching intent's boosts are applied.
#
# To add a new intent, append an IntentRule to INTENT_RULES at the correct
# priority position (more specific intents before more generic ones) and add
# a functional test to verify.

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import logger

# ---------------------------------------------------------------------------
# Intent rule dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntentRule:
    """A single intent detection rule with Solr boost configuration.

    Each rule pairs a regex pattern with the Solr query parameters to inject
    when the pattern matches.  Rules with both main-query and deprecation-query
    boosts participate in both search pipelines.

    Attributes:
        name: Short identifier for logging and testing.
        pattern: Regex pattern matched against the lowercased user query.
        bq: Solr boost query for the main search (replaces any existing bq).
        highlight_terms: Extra terms appended to hl.q for snippet selection.
        dep_title_terms: Solr OR-joined terms for the deprecation allTitle boost.
        dep_content_terms: Solr OR-joined terms for the deprecation main_content boost.
    """

    name: str
    pattern: str
    bq: str = ""
    highlight_terms: str = ""
    dep_title_terms: str = ""
    dep_content_terms: str = ""

    def __post_init__(self) -> None:
        """Validate that deprecation boost fields are either both set or both empty."""
        if bool(self.dep_title_terms) != bool(self.dep_content_terms):
            msg = f"IntentRule {self.name!r}: dep_title_terms and dep_content_terms must both be set or both be empty"
            raise ValueError(msg)

    def matches(self, query: str) -> bool:
        """Return True if the query matches this intent's pattern (case-insensitive)."""
        return bool(re.search(self.pattern, query, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Intent registry
# ---------------------------------------------------------------------------

# Ordered by priority: most specific first, most generic last.
# First match wins for both main-query and deprecation-query boosts.
#
# Priority rationale:
#   - release_date: very specific query type, no overlap with other intents.
#   - eus: specific product lifecycle concept, rarely co-occurs with VM/SPICE.
#   - spice: SPICE queries contain VM terms ("VMs") but need display-protocol
#     boosts (VNC), not VM management boosts (cockpit/virsh).  Must match
#     before the generic VM intent.  See functional test RSPEED_2481.
#   - vm: broadest intent, catches all VM/virtualization queries that didn't
#     match a more specific intent above.
INTENT_RULES: list[IntentRule] = [
    IntentRule(
        name="release_date",
        pattern=r"\b(?:release dates?|released|when was|general availability)\b",
        bq='title:"Enterprise Linux Release Dates"^200 allTitle:"release dates"^30',
    ),
    IntentRule(
        name="eus",
        pattern=r"\b(?:eus|extended update support)\b",
        bq='title:"Enhanced EUS"^100 title:"EUS FAQ"^80',
        highlight_terms='"Enhanced EUS" "48 months" "Enhanced Extended Update Support"',
    ),
    # SPICE is a display/graphics protocol for VMs, NOT a VM management tool.
    # Queries mentioning SPICE need display-protocol-specific boosts (VNC),
    # not VM management boosts (cockpit/virsh).  Without this, queries like
    # "Is SPICE available for VMs?" trigger VM intent, which injects
    # cockpit/virsh highlight terms that flood results with irrelevant
    # VM management content and push out the critical VNC replacement
    # information the LLM needs to answer correctly.
    IntentRule(
        name="spice",
        pattern=r"\bspice\b",
        bq=(
            'allTitle:(spice OR deprecated OR "no longer")^15 '
            'main_content:(VNC OR deprecated OR removed OR replacement OR "no longer")^10'
        ),
        highlight_terms="VNC deprecated removed replacement",
        dep_title_terms='SPICE OR VNC OR "display protocol"',
        dep_content_terms='SPICE OR VNC OR "display protocol"',
    ),
    IntentRule(
        name="vm",
        pattern=r"\b(?:vm|vms|virtual machines?|virtualization|hypervisors?)\b",
        bq=(
            'title:(cockpit OR virtualization OR "virt-manager")^15 '
            'main_content:(cockpit OR "cockpit-machines" OR virsh)^5'
        ),
        highlight_terms="virsh cockpit deprecated virt-manager",
        dep_title_terms='"virt-manager" OR virtualization OR cockpit OR "virtual machine"',
        dep_content_terms='"virt-manager" OR cockpit OR virsh OR "virtual machine"',
    ),
]


# ---------------------------------------------------------------------------
# Boost application
# ---------------------------------------------------------------------------

# Boost weights for deprecation intent terms.
#
# IMPORTANT: Keep these at ^5/^3 or lower.  Higher values (e.g. ^30/^15)
# inflate deprecation query scores well above main query scores (~120),
# causing _filter_by_score to drop ALL main results because they fall
# below 45% of the inflated top score.  The score filter uses raw Solr
# scores which are NOT comparable across queries with different bq boosts.
#
# At ^5/^3, the intent boosts are strong enough to re-rank within the
# deprecation results (e.g. VM deprecation > Vert.x deprecation) without
# overwhelming the cross-query score comparison.
_DEP_TITLE_BOOST = 5
_DEP_CONTENT_BOOST = 3


def apply_main_boosts(params: dict, query_lower: str, cleaned_query: str) -> None:
    """Mutate *params* in-place to add intent-specific bq/hl.q boosts.

    Called after ``_build_main_query`` to layer on intent-specific boosts
    without complicating the base query builder.

    First matching intent wins: the registry is ordered from most specific
    to most generic, so SPICE matches before VM, EUS before VM, etc.
    """
    for rule in INTENT_RULES:
        if rule.matches(query_lower) and rule.bq:
            params["bq"] = rule.bq
            if rule.highlight_terms:
                params["hl.q"] = f"{cleaned_query} {rule.highlight_terms}"
            logger.debug("Intent boost applied: %s", rule.name)
            return


def apply_deprecation_boosts(params: dict, query_lower: str) -> None:
    """Add topic-specific boosts to the deprecation query based on detected intent.

    Without intent-aware boosts, the deprecation query matches ANY content
    containing "deprecated" and "removed", pulling in noise results like
    Eclipse Vert.x release notes or network teaming deprecation notices for
    a VM management question.  Adding intent-specific bq ensures the
    deprecation query surfaces deprecation notices about the user's actual
    topic, not unrelated deprecation content.

    This function APPENDS to the existing bq (the base deprecation term
    boosts defined in _build_deprecation_query) rather than replacing it,
    so the original deprecation/removal title boosts remain active.

    First matching intent wins (evaluated in INTENT_RULES order).
    Queries that match no intent are left unchanged.

    See functional tests RSPEED_2480 (VM management) and RSPEED_2481 (SPICE).
    """
    for rule in INTENT_RULES:
        if not rule.matches(query_lower):
            continue
        # First match wins: if the matched rule has no deprecation terms,
        # stop searching rather than falling through to a less-specific
        # rule.  E.g., "when was virtualization released" matches
        # release_date first (no dep terms) and must not fall through
        # to VM deprecation boosts.
        if not rule.dep_title_terms:
            return
        existing_bq = params.get("bq", "")
        params["bq"] = (
            f"{existing_bq} "
            f"allTitle:({rule.dep_title_terms})^{_DEP_TITLE_BOOST} "
            f"main_content:({rule.dep_content_terms})^{_DEP_CONTENT_BOOST}"
        )
        logger.debug("Deprecation intent boost applied: %s", rule.name)
        return
