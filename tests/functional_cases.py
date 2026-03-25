"""Functional test case data for RSPEED / CLA scenarios (Jira and eval ids)."""

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
    expected_first_doc: str | None = None


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
                "Life Cycle",
            ],
            required_facts=[
                ("ten year", "ten-year", "10 year", "10-year"),
                "full support",
                "maintenance support",
                ("extended life", "extended life phase"),
            ],
            forbidden_claims=[
                "unable to retrieve",
                "has not been released",
            ],
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
    pytest.param(
        FunctionalCase(
            question="What is most current version of Python for RHEL 10?",
            expected_doc_refs=[
                "dynamic_programming_languages",
                "Application Stream",
                "python 3.12",
            ],
            required_facts=[
                "3.12",
            ],
            forbidden_claims=["has not been officially released"],
        ),
        id="RSPEED_2294",
    ),
    pytest.param(
        FunctionalCase(
            question="How to configure RHEL on AWS with Secure Boot?",
            expected_doc_refs=[
                "deploying_rhel_9_on_amazon_web_services",
                "deploying_and_managing_rhel_on_amazon_web_services",
                "Secure Boot",
            ],
            required_facts=[
                "marketplace",
                ("custom image", "custom AMI"),
            ],
            forbidden_claims=["do not expose"],
        ),
        id="RSPEED_2201",
    ),
    pytest.param(
        FunctionalCase(
            question="How to configure 64 hugepages of size 1G at boot time in RHEL 10?",
            expected_doc_refs=[
                "3936101",
                "2791291",
                "hugepage",
            ],
            required_facts=[
                "hugepage",
                "1G",
                ("64", "1024"),
                ("grub", "kernel"),
            ],
            forbidden_claims=["configures hugepages dynamically"],
        ),
        id="RSPEED_2479",
    ),
    pytest.param(
        FunctionalCase(
            question="What are the migration options from Red Hat Fuse to Red Hat build of Apache Camel?",
            expected_doc_refs=[
                "red_hat_fuse",
                "migration",
                "camel",
            ],
            required_facts=[
                ("migration", "migrate"),
                ("fuse", "red hat fuse"),
                ("camel", "apache camel"),
            ],
            forbidden_claims=[],
            expected_first_doc="red hat application services",
        ),
        id="fuse_regression_eol",
    ),
    pytest.param(
        FunctionalCase(
            question="What are the migration options from Red Hat Gluster Storage to Red Hat Ceph Storage?",
            expected_doc_refs=[
                "gluster",
                "migration",
                "ceph storage",
            ],
            required_facts=[
                ("migration", "migrate"),
                ("gluster", "red hat gluster storage"),
                ("ceph", "storage"),
            ],
            forbidden_claims=[],
            expected_first_doc="red hat gluster storage",
        ),
        id="gluster_regression_eol",
    ),
    pytest.param(
        FunctionalCase(
            question="What are the migration options for Red Hat Virtualization to OpenShift Virtualization?",
            expected_doc_refs=[
                "red_hat_virtualization",
                "migration",
                "openshift virtualization",
            ],
            required_facts=[
                ("migration", "migrate"),
                ("openshift", "virtualization"),
                ("rhv", "red hat virtualization"),
            ],
            forbidden_claims=[],
            expected_first_doc="virtualization life cycle",
        ),
        id="rhv_regression_eol",
    ),
    pytest.param(
        FunctionalCase(
            question="What is the list of Red Hat Enterprise Linux for SAP Application Server packages?",
            expected_doc_refs=[
                "red_hat_enterprise_linux_for_sap_applications",
                "package list",
                "SAP",
            ],
            required_facts=[
                "package list",
                "SAP Application Server",
            ],
            forbidden_claims=["not a Red Hat product"],
        ),
        id="RSPEED_1998",
    ),
    pytest.param(
        FunctionalCase(
            question="What are the names of the three RHEL System Roles for SAP used to preconfigure systems?",
            expected_doc_refs=[
                "red_hat_enterprise_linux_system_roles_for_sap",
                "red_hat_enterprise_linux_for_sap_solutions",
                "sap_netweaver_preconfigure",
            ],
            required_facts=[
                "sap_general_preconfigure",
                "sap_netweaver_preconfigure",
                "sap_hana_preconfigure",
                "preconfigure",
            ],
            # Wrong answers often omit sap_netweaver_preconfigure or substitute sap_swpm (install role).
            # We rely on required_facts for the three preconfigure names; avoid forbidding sap_swpm
            # because a good answer may mention install roles separately.
            forbidden_claims=[],
        ),
        id="sap_004",
    ),
]
