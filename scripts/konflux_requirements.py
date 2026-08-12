#!/usr/bin/env python3
"""Generate freeze files for deterministic and hermetic builds.

The following freeze files are created:
  .konflux/requirements.txt            runtime deps (from uv.lock)
  .konflux/requirements-build.txt      build requirements for all dependencies (from requirements-build.in)
  .konflux/requirements-build-pypi.txt packages missing from CI proxy that mush come from PyPI
  .konflux/requirements-build-all.txt  convenience file that includes both requirements-build* files

The hermetic build (Hermeto type:pip) prefetches these.
The Containerfile installs from the offline mirror.
Local/dev builds still use `uv sync`.

Run this whenever uv.lock or the build-system requirement changes, then
commit the regenerated files. CI verifies they are in sync (see Makefile).
"""

# ruff: noqa: S603 -- script runs hardcoded external tools found via PATH

import shutil
import subprocess

from pathlib import Path


UV_BIN = shutil.which("uv") or "uv"
REPO_ROOT = Path(__file__).parents[1]
KONFLUX_DIR = REPO_ROOT / ".konflux"
REQ_FILE = KONFLUX_DIR / "requirements.txt"
BUILD_FILE = KONFLUX_DIR / "requirements-build.txt"
BUILD_ALL_FILE = KONFLUX_DIR / "requirements-build-all.txt"
BUILD_PYPI_FILE = KONFLUX_DIR / "requirements-build-pypi.txt"


def count_packages(path: Path) -> int:
    """Count lines that start with a package name (letter)."""
    return sum(1 for line in path.read_text().splitlines() if line and line[0].isalpha())


def export_deps() -> None:
    """Freeze requirements: uv.lock → requirements.txt.

    --prune drops win32-only transitive deps (pywin32 via mcp, pywin32-ctypes
    via keyring, colorama). uv export emits these with a sys_platform == 'win32'
    marker, but Hermeto prefetch enumerates every line and ignores markers, so it
    tries to fetch pywin32 for Linux, finds no distribution, and fails the build.
    The runtime is always Linux/distroless, so these are never installed.
    """
    subprocess.run(
        [
            UV_BIN,
            "export",
            "--frozen",
            "--no-emit-project",
            "--no-dev",
            "--no-header",
            "--no-annotate",
            "--format",
            "requirements-txt",
            "--prune",
            "colorama",
            "--prune",
            "pywin32",
            "--prune",
            "pywin32-ctypes",
            "-o",
            str(REQ_FILE),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def export_build_deps() -> None:
    """Freeze build requirements: requirements-build.in → requirements-build.txt."""

    # Generate freeze file for all build dependencies.
    # Exclude files that must come from PyPI listed in requirements-build-pypi.in.
    subprocess.run(
        [
            UV_BIN,
            "pip",
            "compile",
            "--generate-hashes",
            "--excludes",
            BUILD_PYPI_FILE,
            "--no-header",
            "--no-annotate",
            "--output-file",
            BUILD_FILE,
            BUILD_FILE.with_suffix(".in"),
        ],
        stdout=subprocess.DEVNULL,
        check=True,
        text=True,
    )

    # Generate freeze file for dependencies that must com from PyPI and are not
    # available through the CI proxy. Do not include indirect dependencies (--no-deps)
    # so that only the minimal number of packages are in this file.
    subprocess.run(
        [
            UV_BIN,
            "pip",
            "compile",
            "--no-deps",
            "--generate-hashes",
            "--no-header",
            "--no-annotate",
            "--output-file",
            BUILD_PYPI_FILE,
            BUILD_PYPI_FILE.with_suffix(".in"),
        ],
        stdout=subprocess.DEVNULL,
        check=True,
        text=True,
    )

    # Add PyPI URL to the beginning of the file.
    with BUILD_PYPI_FILE.open("r+") as f:
        content = f.read()
        f.seek(0)
        f.write("--index-url https://pypi.org/simple/\n\n" + content)

    # Create a single requirements file that points to the other two files for convenience.
    BUILD_ALL_FILE.write_text(f"-r {BUILD_FILE.name}\n-r {BUILD_PYPI_FILE.name}")


def main() -> None:
    """Regenerate all .konflux manifests from uv.lock."""
    print("Creating freeze files...")
    KONFLUX_DIR.mkdir(exist_ok=True)

    export_deps()
    export_build_deps()

    print(f"Wrote {REQ_FILE.relative_to(REPO_ROOT)} ({count_packages(REQ_FILE)} packages)")
    print(f"Wrote {BUILD_FILE.relative_to(REPO_ROOT)} ({count_packages(BUILD_FILE)} packages, hatchling only)")
    print(f"Wrote {BUILD_ALL_FILE.relative_to(REPO_ROOT)} ({count_packages(BUILD_ALL_FILE)} packages, full tree)")
    print(f"Wrote {BUILD_PYPI_FILE.relative_to(REPO_ROOT)} ({count_packages(BUILD_PYPI_FILE)} packages, direct PyPI)")
    print("Remember to commit all files.")


if __name__ == "__main__":
    main()
