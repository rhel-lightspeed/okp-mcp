.PHONY: check-konflux-requirements ci fix format freeze hermeto-clean hermeto-prefetch konflux-requirements lint lock radon rpm-lock setup test typecheck

fix:
	uv run --locked ruff check --fix
	uv run --locked ruff format

lint:
	uv run --locked ruff check src/ tests/

format:
	uv run --locked ruff format src/ tests/

typecheck:
	uv run --locked ty check src/

radon:
	@uv run --locked radon cc src/ -s --min C | grep -q . \
		&& { echo "FAIL: Cyclomatic complexity C or higher detected"; exit 1; } \
		|| echo "PASS: All functions rated A or B"

test:
	uv run --locked pytest

ci: lint typecheck radon check-konflux-requirements test
	@echo ""
	@echo "✅ All CI checks passed!"

# Regenerate the hermetic build manifests from uv.lock.
konflux-requirements:
	python3 scripts/konflux_requirements.py

# Fail if the committed manifests have drifted from uv.lock. Run in CI so a
# lock change without a manifest refresh cannot slip through.
check-konflux-requirements:
	python3 scripts/konflux_requirements.py
	@test -z "$$(git status --porcelain -- .konflux/)" \
		|| { echo "FAIL: .konflux manifests are stale. Run 'make konflux-requirements' and commit."; git status --porcelain -- .konflux/; exit 1; }

setup:
	uv sync --locked --group dev
	pre-commit install

# Run Hermeto locally to validate sdist-only prefetch (no binary annotations).
# Requires podman. Output lands in .hermeto-out/ (gitignored).
# The extra GIT_COMMON_DIR mount handles git worktrees: .git is a file pointing
# to the main repo, so Hermeto needs both paths visible inside the container.
HERMETO_IMAGE ?= ghcr.io/hermetoproject/hermeto:0.56.0
hermeto-prefetch:
	GIT_COMMON=$$(cd "$$(git rev-parse --git-common-dir)" && pwd -P) && \
	podman run --rm \
	  -v "$$(pwd):$$(pwd):z" \
	  -v "$$GIT_COMMON:$$GIT_COMMON:z" \
	  -w "$$(pwd)" \
	  $(HERMETO_IMAGE) fetch-deps \
	  --source . --output ./.hermeto-out \
	  '[{"type": "pip", "path": ".", "requirements_files": [".konflux/requirements.txt"], "requirements_build_files": [".konflux/requirements-build-all.txt", ".konflux/requirements-build-pypi.txt"]}, {"type": "rpm", "path": "."}]'

hermeto-clean:
	rm -rf .hermeto-out/

# Regenerate rpms.lock.yaml from rpms.in.yaml against the builder image.
# Resolves the build-toolchain RPM tree for every target arch so Hermeto can
# prefetch them for hermetic builds. Requires podman; the builder image is read
# straight from the first FROM in Containerfile.
# RLP_IMAGE defaults to the Konflux tool image (needs `podman login quay.io`).
RLP_IMAGE ?= quay.io/konflux-ci/rpm-lockfile-prototype:latest
rpm-lock:
	BUILDER=$$(awk '/^FROM /{print $$2; exit}' Containerfile) && \
	podman run --rm -v "$$(pwd):/work:z" -w /work \
	  $(RLP_IMAGE) --image "$$BUILDER" rpms.in.yaml


lock:
	uv lock

freeze: lock konflux-requirements
