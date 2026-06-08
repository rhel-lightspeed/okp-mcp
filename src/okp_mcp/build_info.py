"""Build-time metadata baked into the container image."""

import os
from importlib.metadata import version


def get_commit_sha() -> str:
    """Return the git commit SHA baked into the container build.

    The Containerfile sets ``COMMIT_SHA`` in the environment via a build arg
    supplied by the Tekton pipeline. Falls back to ``"development"`` for local
    runs where the variable is unset or empty.
    """
    return os.getenv("COMMIT_SHA", "").strip() or "development"


def get_package_version() -> str:
    """Return the installed package version from distribution metadata."""
    return version("okp-mcp")
