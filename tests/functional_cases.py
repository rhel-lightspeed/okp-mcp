"""Functional test case data for document retrieval quality (RSPEED / CLA scenarios).

Each case tests that a single ``_run_portal_search()`` call against a live
Solr instance returns the right documents with the right content.  No LLM
is involved: assertions are fully deterministic.
"""

from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class FunctionalCase:
    """Single functional test scenario for portal search retrieval quality.

    Assertions target the structured ``PortalChunk`` objects returned by
    ``_run_portal_search()``, not LLM-generated prose.  This makes tests
    deterministic: same Solr index, same results every time.

    Fields:
        question: Search query (passed directly to ``_run_portal_search``).
        expected_docs: Substrings matched against each chunk's ``parent_id``,
            ``doc_id``, ``title``, and ``online_source_url``.  At least one
            entry must match at least one result.
        expected_content: Substrings that must appear somewhere in the combined
            chunk text.  Plain strings require exact substring match
            (case-insensitive).  Tuples mean any one alternative must match.
        max_position: If set, at least one expected_docs entry must appear
            within the top N results (1-indexed).
        max_result_count: If set, total results must not exceed this count.
    """

    question: str
    expected_docs: list[str]
    expected_content: list[str | tuple[str, ...]]
    max_position: int | None = None
    max_result_count: int | None = None


FUNCTIONAL_TEST_CASES = [
    # Verified against live Solr 2026-04-03: docs=FAIL content=FAIL
    # Search doesn't surface doc 2726611 (container compat matrix) for this
    # query.  Needs query/boost tuning in portal.py.
    pytest.param(
        FunctionalCase(
            question="Can I run a RHEL 6 container on RHEL 9?",
            expected_docs=[
                "2726611",
                "rhel-container-compatibility",
                "container compatibility matrix",
            ],
            expected_content=["unsupported", "compatibility matrix"],
        ),
        id="RSPEED_2482",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="Is SPICE available to help with RHEL VMs?",
            expected_docs=["6955095", "6999469", "spice"],
            expected_content=[("deprecated", "removed"), "vnc"],
        ),
        id="RSPEED_2481",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="What is the recommended tool for managing VMs in RHEL?",
            expected_docs=[
                "6906941",
                "cockpit-machines",
                "configuring_and_managing_virtualization",
            ],
            expected_content=["cockpit", "virsh", "deprecated"],
        ),
        id="RSPEED_2480",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="How long is an EUS release supported for?",
            expected_docs=["rhel9-eus-faq", "rhel-eus", "updates/errata"],
            expected_content=[
                "24 months",
                ("enhanced eus", "enhanced extended update support"),
                ("48 months", "4 years"),
            ],
        ),
        id="RSPEED_2479",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="Which RHEL 9 releases have EUS available?",
            expected_docs=["rhel9-eus-faq", "rhel-eus", "updates/errata"],
            expected_content=[
                "9.0",
                "9.2",
                "9.4",
                "9.6",
            ],
        ),
        id="RSPEED_2478",
    ),
    # Solr data gap: the RHEL Life Cycle page's JS-rendered dates table is
    # not captured in the Solr index, so no lifecycle doc exists to match.
    # Unfixable without Solr data changes.  Tracked by RSPEED-2701 / RHOKP-1208.
    pytest.param(
        FunctionalCase(
            question="How long is RHEL 10 supported?",
            expected_docs=[
                "updates/errata",
                "Life Cycle",
            ],
            expected_content=[
                ("ten year", "ten-year", "10 year", "10-year"),
                "full support",
                "maintenance support",
                ("extended life", "extended life phase"),
            ],
        ),
        id="RSPEED_2698",
        marks=pytest.mark.xfail(reason="Solr data gap: lifecycle page not indexed (RHOKP-1208)"),
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="When was RHEL 10 released?",
            expected_docs=[
                "red-hat-enterprise-linux-release-dates",
                "release-dates",
            ],
            expected_content=[
                ("2025-05-20", "May 20, 2025", "May 20"),
                "10.0",
            ],
        ),
        id="RSPEED_2697",
    ),
    # Verified against live Solr 2026-04-03: docs=FAIL content=FAIL
    # Search returns JBoss EAP/XFS docs instead of Python RHEL 10 content.
    # Needs query/boost tuning in portal.py.
    pytest.param(
        FunctionalCase(
            question="What is most current version of Python for RHEL 10?",
            expected_docs=[
                "dynamic_programming_languages",
                "Application Stream",
                "python 3.12",
            ],
            expected_content=[
                "3.12",
            ],
        ),
        id="RSPEED_2294",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="How to configure RHEL on AWS with Secure Boot?",
            expected_docs=[
                "deploying_rhel_9_on_amazon_web_services",
                "deploying_and_managing_rhel_on_amazon_web_services",
                "Secure Boot",
            ],
            expected_content=[
                "marketplace",
                ("custom image", "custom AMI", "custom RHEL image", "custom RHEL AMI"),
            ],
        ),
        id="RSPEED_2201",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="How to configure 64 hugepages of size 1G at boot time in RHEL 10?",
            expected_docs=[
                "3936101",
                "2791291",
                "hugepage",
            ],
            expected_content=[
                "hugepage",
                "1G",
                ("64", "1024"),
                ("grub", "kernel"),
            ],
        ),
        id="RSPEED_2200",
    ),
    # Verified against live Solr 2026-04-03: PASS (docs + content)
    # NOTE: expected_first_doc may not match; first result varies.
    pytest.param(
        FunctionalCase(
            question="What are the migration options from Red Hat Fuse to Red Hat build of Apache Camel?",
            expected_docs=[
                "red_hat_fuse",
                "migration",
                "camel",
            ],
            expected_content=[
                ("migration", "migrate"),
                ("fuse", "red hat fuse"),
                ("camel", "apache camel"),
            ],
        ),
        id="fuse_regression_eol",
    ),
    # Verified against live Solr 2026-04-03: docs=PASS
    # Chunks mention "upgrade" rather than "migration" for Gluster.
    pytest.param(
        FunctionalCase(
            question="What are the migration options from Red Hat Gluster Storage to Red Hat Ceph Storage?",
            expected_docs=[
                "gluster",
                "migration",
                "ceph storage",
            ],
            expected_content=[
                ("migration", "migrate", "upgrade"),
                ("gluster", "red hat gluster storage"),
                ("ceph", "storage"),
            ],
        ),
        id="gluster_regression_eol",
    ),
    # Verified against live Solr 2026-04-03: PASS (docs + content)
    # NOTE: expected_first_doc may not match; first result varies.
    pytest.param(
        FunctionalCase(
            question="What are the migration options for Red Hat Virtualization to OpenShift Virtualization?",
            expected_docs=[
                "red_hat_virtualization",
                "migration",
                "openshift virtualization",
            ],
            expected_content=[
                ("migration", "migrate"),
                ("openshift", "virtualization"),
                ("rhv", "red hat virtualization"),
            ],
        ),
        id="rhv_regression_eol",
    ),
    # Verified against live Solr 2026-04-03: docs=PASS
    # Chunks mention "SAP Solutions" rather than "SAP Application Server".
    pytest.param(
        FunctionalCase(
            question="What is the list of Red Hat Enterprise Linux for SAP Application Server packages?",
            expected_docs=[
                "red_hat_enterprise_linux_for_sap_applications",
                "package list",
                "SAP",
            ],
            expected_content=[
                ("package list", "packages"),
                ("SAP Application", "SAP Solutions", "for SAP"),
            ],
        ),
        id="RSPEED_1998",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="What are the names of the three RHEL System Roles for SAP used to preconfigure systems?",
            expected_docs=[
                "red_hat_enterprise_linux_system_roles_for_sap",
                "red_hat_enterprise_linux_for_sap_solutions",
                "sap_netweaver_preconfigure",
            ],
            expected_content=[
                ("sap_general_preconfigure", "sap-general-preconfigure", "sap-preconfigure"),
                ("sap_netweaver_preconfigure", "sap-netweaver-preconfigure"),
                ("sap_hana_preconfigure", "sap-hana-preconfigure"),
                "preconfigure",
            ],
        ),
        id="sap_004",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="How to prepare a custom SELinux policy based on AVC messages?",
            expected_docs=[
                "58792",
                "5494701",
                "audit2allow",
            ],
            expected_content=[
                "audit2allow",
                ("ausearch", "audit.log"),
                "semodule",
            ],
        ),
        id="RSPEED_2136",
    ),
    # Verified against live Solr 2026-04-03: PASS
    # Root cause (historical): solution 45950 had its highlight snippet
    # truncated by RRF.  Fixed by keeping the longest chunk per doc_id
    # in _reciprocal_rank_fusion().
    pytest.param(
        FunctionalCase(
            question="How to enable bnxt_en NIC driver debugging?",
            expected_docs=["45950"],
            expected_content=[
                "msglvl",
                ("ethtool -s", "ethtool --change"),
            ],
            # Wrong command from a different solution doc.
        ),
        id="RSPEED_2123",
    ),
    # Verified against live Solr 2026-04-03: docs=PASS content=FAIL
    # RHBA errata match "updates/errata" but chunks don't contain the
    # specific date.  Doc 7005471 (lifecycle page) not in results.
    # RHBA errata contain "End of Maintenance Support" but not the exact date.
    pytest.param(
        FunctionalCase(
            question="When does the maintenance support phase end for RHEL 7?",
            expected_docs=[
                "7005471",
                "updates/errata",
                "end of maintenance",
            ],
            expected_content=[
                ("end of maintenance", "product retirement", "June 30, 2024", "June 2024"),
            ],
        ),
        id="RSPEED_2745",
    ),
    # Verified against live Solr 2026-04-03: docs=FAIL content=FAIL
    # Search returns OCP/RHEL release notes instead of bonding solution
    # docs.  Long natural-language query with specific params needs
    # query tuning.
    pytest.param(
        FunctionalCase(
            question=(
                "configure lacp bond with name prod and NIC of bond are ens6 and ens8,"
                " lacp rate is slow, ip of bond is 192.9.8.3/24, gateway 192.9.8.1."
                " provide commands with nmcli"
            ),
            expected_docs=[
                "7134402",
                "5069791",
                "1526613",
            ],
            expected_content=[
                ("802.3ad", "mode=4"),
                ("bond-slave", "bond-port", "master prod"),
                "lacp_rate",
            ],
        ),
        id="RSPEED_2113",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="how do i update the kernel arguments on a system using rpm-ostree?",
            expected_docs=[
                "7069583",
                "using_image_mode_for_rhel",
                "kargs",
            ],
            expected_content=[
                "rpm-ostree kargs",
                ("--append", "append"),
                ("--delete", "--replace"),
            ],
            # Wrong tool; would come from grubby-focused docs.
        ),
        id="RSPEED_1931",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="Is GFS2 available in RHEL 10?",
            expected_docs=[
                "7092011",
                "3290201",
                "considerations_in_adopting_rhel_10",
            ],
            expected_content=[
                ("removed", "discontinued"),
                "resilient storage",
                "gfs2",
            ],
        ),
        id="RSPEED_2794",
    ),
    # Verified against live Solr 2026-04-03: PASS
    pytest.param(
        FunctionalCase(
            question="How to mount a kerberos CIFS share at boot time using a keytab?",
            expected_docs=[
                "6113681",
                "262553",
                "cifs",
            ],
            expected_content=[
                ("sec=krb5", "krb5"),
                "keytab",
                ("fstab", "boot"),
            ],
        ),
        id="RSPEED_1902",
    ),
    # Verified against live Solr 2026-04-06: PASS
    # Solution 32530 clearly states "Shrinking is not supported on a GFS2
    # or XFS file system, so you cannot reduce the size of a logical volume
    # that contains a GFS2 or XFS file system."
    pytest.param(
        FunctionalCase(
            question="how do I shrink an LVM volume with XFS on it",
            expected_docs=[
                "32530",
                "shrink",
            ],
            expected_content=[
                ("not supported", "cannot"),
                "xfs",
                ("lvreduce", "shrink"),
            ],
        ),
        id="RSPEED_1582",
    ),
    # Verified against live Solr 2026-04-06: PASS
    # Solution 3592 documents the correct `sos report` command (not the
    # deprecated `sosreport` binary) and the --upload option.
    pytest.param(
        FunctionalCase(
            question="Can sos automatically upload the generated archive to Red Hat Customer Portal?",
            expected_docs=[
                "3592",
                "2112",
                "sos",
            ],
            expected_content=[
                "sos report",
                "--upload",
                ("customer portal", "red hat support"),
            ],
        ),
        id="RSPEED_1739",
    ),
    # Verified against live Solr 2026-04-06: PASS
    # btrfs removal content surfaces via solution 887853 (btrfs in RHEL 7)
    # and solution 58533 (filesystem compression / btrfs technology preview).
    pytest.param(
        FunctionalCase(
            question="how do I use the btrfs filesystem in RHEL10",
            expected_docs=[
                "7020130",
                "197643",
                "btrfs",
            ],
            expected_content=[
                ("removed", "not supported", "not available", "technology preview"),
                "btrfs",
            ],
        ),
        id="RSPEED_1584",
    ),
    # Verified against live Solr 2026-04-06: docs=PASS content=PASS
    # grub2-mkconfig docs surface via solutions 3447531 and 7063369.
    # Noise from JBoss EAP deprecation articles in deprecation side-query
    # but core grub content is present.
    pytest.param(
        FunctionalCase(
            question="How do I recreate the grub configuration file",
            expected_docs=[
                "3447531",
                "grub",
            ],
            expected_content=[
                "grub2-mkconfig",
                ("grub.cfg", "grub2"),
            ],
        ),
        id="RSPEED_1726",
    ),
    # Verified against live Solr 2026-04-06: docs=PASS content=PARTIAL
    # Search returns subscription management articles but mixed with
    # unrelated JBoss deprecation noise.  Solution 1282753 covers
    # Satellite registration; article 11258 covers RHSM updates flow.
    pytest.param(
        FunctionalCase(
            question="How can I register my RHEL system to Red Hat server using CLI?",
            expected_docs=[
                "1282753",
                "11258",
                "subscription-manager",
            ],
            expected_content=[
                "subscription-manager",
                ("register", "registration"),
            ],
        ),
        id="RSPEED_1813",
    ),
    # Verified against live Solr 2026-04-07: PASS
    # Solution 6906941 (Cockpit) surfaces first with "This module deprecates
    # virt-manager tool".  The RHEL 9.0 Release Notes contain the authoritative
    # deprecation statement but rank too low due to BM25 length normalization
    # on the massive release notes document.
    pytest.param(
        FunctionalCase(
            question="Can I use virt-manager to manage virtual machines in RHEL 9?",
            expected_docs=[
                "6906941",
                "virt-manager",
            ],
            expected_content=[
                ("deprecated", "deprecates"),
                "cockpit",
            ],
        ),
        id="virt_manager_rhel9_deprecated",
    ),
    # Verified against live Solr 2026-04-07: PASS
    # CLA said rpm-ostree can't install packages; correct answer is
    # `rpm-ostree install $package` followed by a reboot.
    pytest.param(
        FunctionalCase(
            question="how do i install a package on a system using rpm-ostree?",
            expected_docs=[
                "3297891",
                "composing_installing_and_managing_rhel_for_edge_images",
                "rpm-ostree",
            ],
            expected_content=[
                "rpm-ostree install",
                ("reboot", "restart"),
                ("package", "packages"),
            ],
        ),
        id="RSPEED_1930",
    ),
    # Verified against live Solr 2026-04-07: PASS
    # CLA gave fabricated commands (rpm-ostree split --list, rpm-ostree
    # transaction rollback); correct answer is `rpm-ostree rollback` + reboot.
    # Article 5719641 (RHEL for Edge videos) surfaces the correct command
    # but its highlight snippet is a video description, too brief for
    # "reboot" or "deployment" terms.
    pytest.param(
        FunctionalCase(
            question="how do i rollback my system to a previous version using rpm-ostree",
            expected_docs=[
                "5719641",
                "composing_installing_and_managing_rhel_for_edge_images",
                "rpm-ostree",
            ],
            expected_content=[
                "rpm-ostree rollback",
                "previous",
            ],
        ),
        id="RSPEED_1929",
    ),
    # Verified against live Solr 2026-04-07: PASS
    # CLA said to use `rpm-ostree branch`; correct answer is
    # `rpm-ostree status` to list deployments.
    pytest.param(
        FunctionalCase(
            question="how do i see the list of deployments on a system using rpm-ostree?",
            expected_docs=[
                "5719641",
                "composing_installing_and_managing_rhel_for_edge_images",
                "rpm-ostree",
            ],
            expected_content=[
                "rpm-ostree status",
                ("deployment", "deployments"),
            ],
        ),
        id="RSPEED_1859",
    ),
]
