"""Functional test case data for RSPEED CLA scenarios."""

from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class FunctionalCase:
    """Single functional test scenario from a RSPEED Jira ticket.

    required_facts entries can be a plain string (exact substring match) or a
    tuple of strings (any one of them must appear in the response).
    """

    question: str
    expected_doc_refs: list[str]
    required_facts: list[str | tuple[str, ...]]
    forbidden_claims: list[str]


FUNCTIONAL_TEST_CASES = [
    pytest.param(
        FunctionalCase(
            question="Can I run a RHEL 6 container on RHEL 9?",
            expected_doc_refs=[
                "2726611",
                "rhel-container-compatibility",
                "container compatibility matrix",
            ],
            required_facts=["unsupported", "compatibility matrix"],
            forbidden_claims=["viable strategy"],
        ),
        id="RSPEED_2482",
    ),
    pytest.param(
        FunctionalCase(
            question="Is SPICE available to help with RHEL VMs?",
            expected_doc_refs=["6955095", "6999469", "spice"],
            required_facts=[("deprecated", "removed"), "vnc"],
            forbidden_claims=["fully supported and commonly used"],
        ),
        id="RSPEED_2481",
    ),
    pytest.param(
        FunctionalCase(
            question="What is the recommended tool for managing VMs in RHEL?",
            expected_doc_refs=[
                "6906941",
                "cockpit-machines",
                "configuring_and_managing_virtualization",
            ],
            required_facts=["cockpit", "virsh", "deprecated"],
            forbidden_claims=["enterprise-grade"],
        ),
        id="RSPEED_2480",
    ),
    pytest.param(
        FunctionalCase(
            question="How long is an EUS release supported for?",
            expected_doc_refs=["rhel9-eus-faq", "rhel-eus", "updates/errata"],
            required_facts=[
                "24 months",
                ("enhanced eus", "enhanced extended update support"),
                ("48 months", "4 years"),
            ],
            forbidden_claims=["30 months"],
        ),
        id="RSPEED_2479",
    ),
    pytest.param(
        FunctionalCase(
            question="Which RHEL 9 releases have EUS available?",
            expected_doc_refs=["rhel9-eus-faq", "rhel-eus", "updates/errata"],
            required_facts=[
                "9.0",
                "9.2",
                "9.4",
                "9.6",
            ],
            forbidden_claims=["9.0 did not have EUS"],
        ),
        id="RSPEED_2478",
    ),
    pytest.param(
        FunctionalCase(
            question="How long is RHEL 10 supported?",
            expected_doc_refs=[
                "updates/errata",
                "7126291",
                "life cycle",
            ],
            required_facts=[
                ("ten year", "ten-year", "10 year", "10-year"),
                "Full Support",
                "Maintenance Support",
            ],
            forbidden_claims=[],
        ),
        id="RSPEED_2698",
    ),
    pytest.param(
        FunctionalCase(
            question="When was RHEL 10 released?",
            expected_doc_refs=[
                "red-hat-enterprise-linux-release-dates",
                "release-dates",
            ],
            required_facts=[
                ("2025-05-20", "May 20, 2025", "May 20"),
                "10.0",
            ],
            forbidden_claims=["has not been released"],
        ),
        id="RSPEED_2697",
    ),
]
