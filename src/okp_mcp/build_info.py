"""Build-time metadata baked into the container image."""

from importlib.metadata import version

# Absolute path matching the Containerfile WORKDIR (/app).
# Using an absolute path avoids breakage if Kubernetes overrides workingDir.
_COMMIT_SHA_PATH = "/app/COMMIT_SHA"


def get_commit_sha() -> str:
    """Read the git commit SHA written during the container build.

    The Containerfile writes the SHA to ``/app/COMMIT_SHA`` via a build arg
    supplied by the Tekton pipeline.  Falls back to ``"development"`` for
    local runs where the file does not exist or is unreadable.
    """
    try:
        content = open(_COMMIT_SHA_PATH).read().strip()  # noqa: SIM115 -- one-shot read, no resource leak
        return content or "development"
    except OSError:
        return "development"


def get_package_version() -> str:
    """Return the installed package version from distribution metadata."""
    return version("okp-mcp")
