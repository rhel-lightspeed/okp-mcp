#!/bin/bash
# scripts/install-toolchain.sh — Install Python + C/Rust build toolchain for from-source builds.
#
# Used by Containerfile-source to compile all Python wheels from source instead
# of manylinux prebuilt wheels (Product Security requirement).
#
# In hermetic Konflux builds the toolchain RPMs are prefetched by Hermeto
# (rpms.in.yaml / rpms.lock.yaml) and Konflux injects a file:// repo into
# /etc/yum.repos.d. The base image's repos point at network URLs that are
# unreachable under --network none, so we detect the Hermeto-injected repo
# and restrict dnf to it. Local non-hermetic builds keep the image's network
# repos and resolve from the UBI CDN.
#
# --allowerasing is required because the UBI 10 base image ships
# glibc-minimal-langpack which conflicts with glibc-devel's glibc dependency
# when a newer glibc is available in the repo.
set -euo pipefail

dnf_args=(--allowerasing)

if [ -f /cachi2/cachi2.env ]; then
    # Find the Hermeto-injected repo (name varies); disable all others.
    hermeto_repo=$(grep -lm1 'file:///cachi2' /etc/yum.repos.d/*.repo 2>/dev/null | head -1)
    if [ -n "$hermeto_repo" ]; then
        repo_id=$(grep -oP '(?<=\[)[^\]]+' "$hermeto_repo" | head -1)
        dnf_args+=(--disablerepo='*' --enablerepo="$repo_id")
    fi
fi

# NOTE: if you change this package list, update rpms.in.yaml too and run
# `make rpm-lock` to regenerate rpms.lock.yaml for hermetic builds.
dnf install -y "${dnf_args[@]}" \
    python3.12 \
    python3.12-pip \
    python3-devel \
    gcc \
    gcc-c++ \
    openssl-devel \
    rust \
    cargo \
    pkgconf \
    libffi-devel

dnf clean all
