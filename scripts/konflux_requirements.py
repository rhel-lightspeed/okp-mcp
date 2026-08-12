#!/usr/bin/env python3
"""Generate freeze files for deterministic and hermetic builds.

The following freeze files are created:
  .konflux/requirements.txt            runtime deps (from uv.lock)
  .konflux/requirements-build.txt      direct build requirements (should match pyproject.toml)
  .konflux/requirements-build-all.txt  build requirements for this project and indirect dependencies
  .konflux/hermeto/build.txt           build deps served by the CI proxy
  .konflux/hermeto/build-pypi.txt      build deps that must come from PyPI

pip reads the .konflux/*.txt files (index resolved from the offline mirror).
Hermeto reads the .konflux/hermeto/*.txt files, which are flat and split by
index because Hermeto scopes --index-url per file and ignores nested -r.

The hermetic build (Hermeto type:pip) prefetches these.
The Containerfile installs from the offline mirror.
Local/dev builds still use `uv sync`.

Run this whenever uv.lock or the build-system requirement changes, then
commit the regenerated files. CI verifies they are in sync (see Makefile).
"""

# ruff: noqa: S603 -- script runs hardcoded external tools found via PATH

import shutil
import subprocess

from itertools import chain
from pathlib import Path


UV_BIN = shutil.which("uv") or "uv"
REPO_ROOT = Path(__file__).parents[1]
KONFLUX_DIR = REPO_ROOT / ".konflux"
REQ_FILE = KONFLUX_DIR / "requirements.txt"
BUILD_FILE = KONFLUX_DIR / "requirements-build.txt"
BUILD_ALL_FILE = KONFLUX_DIR / "requirements-build-all.txt"
BUILD_PYPI_FILE = KONFLUX_DIR / "requirements-build-pypi.txt"
HERMETO_DIR = KONFLUX_DIR / "hermeto"
HERMETO_BUILD_FILE = HERMETO_DIR / "build.txt"
HERMETO_BUILD_PYPI_FILE = HERMETO_DIR / "build-pypi.txt"


def count_packages(path: Path) -> int:
    """Count lines that start with a package name (letter)."""
    return sum(1 for line in path.read_text().splitlines() if line and line[0].isalpha())


def clean() -> None:
    for file in KONFLUX_DIR.rglob("*.txt"):
        file.unlink()


def export_deps() -> None:
    """Freeze requirements: uv.lock → requirements.txt.

    --prune drops win32-only transitive deps (pywin32 via mcp, pywin32-ctypes
    via keyring, colorama). uv export emits these with a sys_platform == 'win32'
    marker, but Hermeto prefetch enumerates every line and ignores markers, so it
    tries to fetch pywin32 for Linux, finds no distribution, and fails the build.
    The runtime is always Linux/distroless, so these are never installed.
    """
    prune = ("colorama", "pywin32", "pywin32-ctypes")
    prune_args = list(chain.from_iterable(("--prune", package) for package in prune))
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
            *prune_args,
            "-o",
            str(REQ_FILE),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def export_build_deps() -> None:
    """Freeze build requirements: requirements-build.in → requirements-build.txt."""

    # Generate freeze file for direct build dependencies.
    subprocess.run(
        [
            UV_BIN,
            "pip",
            "compile",
            "--generate-hashes",
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

    # Generate freeze file for direct and indirect build requirements.
    # Use two requirements files as input since only heremeto needs separate
    # freeze files.
    subprocess.run(
        [
            UV_BIN,
            "pip",
            "compile",
            "--generate-hashes",
            "--no-header",
            "--no-annotate",
            "--output-file",
            BUILD_ALL_FILE,
            BUILD_ALL_FILE.with_suffix(".in"),
            BUILD_PYPI_FILE.with_suffix(".in"),
        ],
        stdout=subprocess.DEVNULL,
        check=True,
        text=True,
    )


def export_hermeto_files() -> None:
    """Freeze Hermeto build requirements into .konflux/hermeto/.

    Hermeto scopes --index-url per file so the build deps are split into two
    flat files: build.txt for packages served by the CI proxy (default index)
    and build-pypi.txt for packages that must come from PyPI.
    """

    # Generate freeze file for build dependencies available through the CI proxy.
    # Exclude packages listed in requirements-build-pypi.in.
    subprocess.run(
        [
            UV_BIN,
            "pip",
            "compile",
            "--generate-hashes",
            "--excludes",
            BUILD_PYPI_FILE.with_suffix(".in"),
            "--no-header",
            "--no-annotate",
            "--output-file",
            HERMETO_BUILD_FILE,
            BUILD_ALL_FILE.with_suffix(".in"),
        ],
        stdout=subprocess.DEVNULL,
        check=True,
        text=True,
    )

    # Generate freeze file for dependencies that must come from PyPI and are not
    # available through the CI proxy. Indirect dependencies are skipped (--no-deps)
    # so that indirect dependencies are not listed in this file.
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
            HERMETO_BUILD_PYPI_FILE,
            BUILD_PYPI_FILE.with_suffix(".in"),
        ],
        stdout=subprocess.DEVNULL,
        check=True,
        text=True,
    )

    # Add PyPI URL to the beginning of the file.
    with HERMETO_BUILD_PYPI_FILE.open("r+") as f:
        content = f.read()
        f.seek(0)
        f.write("--index-url https://pypi.org/simple/\n\n" + content)


def main() -> None:
    """Regenerate all .konflux manifests from uv.lock."""
    print("Creating freeze files...")
    KONFLUX_DIR.mkdir(exist_ok=True)
    HERMETO_DIR.mkdir(exist_ok=True)

    clean()
    export_deps()
    export_build_deps()
    export_hermeto_files()

    print(f"Wrote {REQ_FILE.relative_to(REPO_ROOT)} ({count_packages(REQ_FILE)} packages)")
    print(f"Wrote {BUILD_FILE.relative_to(REPO_ROOT)} ({count_packages(BUILD_FILE)} packages, hatchling only)")
    print(f"Wrote {BUILD_ALL_FILE.relative_to(REPO_ROOT)} ({count_packages(BUILD_ALL_FILE)} packages, full tree)")
    print(f"Wrote {HERMETO_BUILD_FILE.relative_to(REPO_ROOT)} ({count_packages(HERMETO_BUILD_FILE)} packages, proxy)")
    pypi_count = count_packages(HERMETO_BUILD_PYPI_FILE)
    print(f"Wrote {HERMETO_BUILD_PYPI_FILE.relative_to(REPO_ROOT)} ({pypi_count} packages, direct PyPI)")
    print("Remember to commit all files.")


if __name__ == "__main__":
    main()
