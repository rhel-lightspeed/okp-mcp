"""Build-time metadata baked into the container image."""

import logging
import os
import subprocess  # noqa: S404 -- used only for a fixed, no-input `git rev-parse` in local dev
from importlib.metadata import version
from pathlib import Path

# In-container WORKDIR set by the Containerfile. This is the path *inside the
# built image*, not a directory on a developer's machine. The Tekton pipeline
# writes the build commit to ``$APP_ROOT/COMMIT_SHA`` during the image build, so
# this default only resolves when running inside the container.
DEFAULT_APP_ROOT = os.getenv("APP_ROOT", "/opt/app-root/src")
DEFAULT_COMMIT_SHA = os.getenv("COMMIT_SHA", "development")

logger = logging.getLogger(__name__)


def _commit_sha_from_git() -> str:
    """Return the short commit SHA from the local git checkout, if available.

    Local dev runs (``uv run okp-mcp``) execute outside the container, so the
    baked ``$APP_ROOT/COMMIT_SHA`` file does not exist. Rather than reporting a
    useless ``"development"`` sentinel, ask git directly so startup logs and
    Sentry get the real commit. Returns ``None`` when git is unavailable or the
    working directory is not a repo (e.g. an unpacked sdist).
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 -- git resolved from PATH is fine for dev-only use
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        raise ValueError("Failed to retrieve git commit sha.") from ex

    return result.stdout.strip()


def get_commit_sha() -> str:
    """Return the git commit SHA for this build.

    Resolution order:

    1. ``COMMIT_SHA`` environment variable (explicit runtime override).
    2. ``$APP_ROOT/COMMIT_SHA`` file baked into the container image by Tekton.
    3. ``git rev-parse`` against the local checkout, for dev runs outside a
       container where neither of the above is present.
    4. ``"development"`` when none of the above yield a SHA.
    """
    commit_sha_file = Path(DEFAULT_APP_ROOT, "COMMIT_SHA")
    commit_sha = DEFAULT_COMMIT_SHA
    try:
        commit_sha = commit_sha_file.read_text(encoding="utf-8").strip()
    except OSError:
        commit_sha = _commit_sha_from_git()
        logger.warning(f"No commit sha found in {commit_sha_file}. Using commit value: {commit_sha}")

    return commit_sha


def get_package_version() -> str:
    """Return the installed package version from distribution metadata."""
    return version("okp-mcp")
