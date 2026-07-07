#!/usr/bin/env python3
"""Regenerate Hermeto-consumable dependency manifests for hermetic Konflux builds.

uv.lock stays the single source of truth. These manifests are generated
artifacts derived from it:
  .konflux/requirements.txt          runtime deps (hash-pinned, from uv.lock)
  .konflux/requirements-build.txt    transitive build deps (hash-pinned, hatchling only)
  .konflux/requirements-build-all.txt  full transitive build tree (hash-pinned)
  .konflux/requirements-build-pypi.txt packages missing from Konflux proxy (direct PyPI)

The hermetic build (Hermeto type:pip) prefetches these; the Containerfile
installs from the offline mirror. Local/dev builds still use `uv sync`.

Run this whenever uv.lock or the build-system requirement changes, then
commit the regenerated files. CI verifies they are in sync (see Makefile).
"""

# ruff: noqa: S603 -- script runs hardcoded external tools found via PATH

import re
import shutil
import subprocess

from pathlib import Path


UV_BIN = shutil.which("uv") or "uv"
KONFLUX_DIR = Path(__file__).parents[1] / ".konflux"
REQ_FILE = KONFLUX_DIR / "requirements.txt"
BUILD_FILE = KONFLUX_DIR / "requirements-build.txt"
BUILD_ALL_FILE = KONFLUX_DIR / "requirements-build-all.txt"
BUILD_PYPI_FILE = KONFLUX_DIR / "requirements-build-pypi.txt"

# Pin uv-build to a version compatible with UBI 10's rustc (1.92). pybuild-deps
# resolves the latest version, but uv-build >=0.11.8 requires rustc >=1.93 and
# the from-source hermetic build compiles it from sdist. 0.11.7 is the newest
# release with MSRV 1.92. py-key-value-aio accepts >=0.11.4,<0.12.
#
# This cannot be a pyproject.toml constraint-dependency because pybuild-deps
# does its own resolution and does not read uv's constraint configuration.
# The only way to force a specific version is text replacement in the output.
UV_BUILD_PIN = (
    "uv-build==0.11.7 \\\n    --hash=sha256:258e3a10929b5de79074078ba1ad8edbdd4db5d9c3cafba81f11b329eeaaca08\n"
)

# Packages the Konflux artifact registry proxy cannot find. These go into a
# separate file with --index-url pointing directly at PyPI. Add new package
# names here as failures are discovered.
PROXY_MISSING = re.compile(r"^(setuptools-rust|vcs-versioning)==")


def count_packages(path: Path) -> int:
    """Count lines that start with a package name (letter)."""
    return sum(1 for line in path.read_text().splitlines() if line and line[0].isalpha())


def export_runtime_deps() -> None:
    """Export runtime deps from uv.lock → requirements.txt.

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
    """Export hatchling build backend deps → requirements-build.txt."""
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
            "-",
        ],
        input="hatchling\n",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        text=True,
    )


def export_full_build_tree() -> None:
    """Export full transitive build tree → requirements-build-all.txt.

    pybuild-deps reads requirements.txt and pyproject.toml build-system.requires,
    resolves every PEP 517 build backend needed to compile each sdist, and outputs
    a hash-pinned manifest.
    """
    (Path.home() / ".cache" / "pybuild-deps").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            UV_BIN,
            "tool",
            "run",
            "pybuild-deps",
            "compile",
            "--generate-hashes",
            "--no-header",
            "--no-annotate",
            "-o",
            str(BUILD_ALL_FILE),
            str(REQ_FILE),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def pin_uv_build() -> None:
    """Replace the uv-build entry in requirements-build-all.txt with a pinned version.

    The pinned version is compatible with UBI 10's rustc (1.92). pybuild-deps
    resolves the latest, but uv-build >=0.11.8 needs rustc >=1.93.
    """
    text = BUILD_ALL_FILE.read_text()

    # Match: "uv-build==<version> \" followed by continuation "--hash=" lines,
    # then the next non-continuation line (or EOF).
    text, count = re.subn(
        r"^uv-build==.*?\n(?:    --hash=.*\n)*",
        UV_BUILD_PIN,
        text,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise RuntimeError("uv-build entry not found in " + str(BUILD_ALL_FILE))

    BUILD_ALL_FILE.write_text(text)


def split_proxy_missing() -> None:
    """Split packages missing from the Konflux proxy into a separate file.

    Packages matching PROXY_MISSING go to requirements-build-pypi.txt with an
    --index-url header pointing directly at PyPI. The rest stay in
    requirements-build-all.txt.
    """
    keep_lines: list[str] = []
    pypi_lines: list[str] = []
    target = keep_lines  # current output target

    for line in BUILD_ALL_FILE.read_text().splitlines(keepends=True):
        # Package lines start with a letter; continuation lines start with spaces
        if line and line[0].isalpha():
            target = pypi_lines if PROXY_MISSING.match(line) else keep_lines
        target.append(line)

    BUILD_ALL_FILE.write_text("".join(keep_lines))
    BUILD_PYPI_FILE.write_text("--index-url https://pypi.org/simple/\n\n" + "".join(pypi_lines))


def main() -> None:
    """Regenerate all .konflux manifests from uv.lock."""
    print("Creating freeze files...")
    KONFLUX_DIR.mkdir(exist_ok=True)

    export_runtime_deps()
    export_build_deps()
    export_full_build_tree()
    pin_uv_build()
    split_proxy_missing()

    print(f"Wrote {REQ_FILE} ({count_packages(REQ_FILE)} packages)")
    print(f"Wrote {BUILD_FILE} ({count_packages(BUILD_FILE)} packages, hatchling only)")
    print(f"Wrote {BUILD_ALL_FILE} ({count_packages(BUILD_ALL_FILE)} packages, full tree)")
    print(f"Wrote {BUILD_PYPI_FILE} ({count_packages(BUILD_PYPI_FILE)} packages, direct PyPI)")
    print("Remember to commit all four files.")


if __name__ == "__main__":
    main()
