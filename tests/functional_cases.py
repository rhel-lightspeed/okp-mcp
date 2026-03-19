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
]
