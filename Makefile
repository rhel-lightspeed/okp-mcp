# Determine container runtime, preferring Docker on macOS
OS = $(shell uname)
CONTAINER_RUNTIMES = podman docker
ifeq ($(OS), Darwin)
	CONTAINER_RUNTIMES = docker podman
endif

CONTAINER_RUNTIME ?= $(shell type -P $(CONTAINER_RUNTIMES) | head -n 1)
RLP_VERSION = 0.30.1
RLP_IMAGE ?= rpm-lockfile-prototype:$(RLP_VERSION)
BUILDER ?= $(shell awk '/^FROM /{print $$2; exit}' Containerfile)

.PHONY: check-konflux-requirements ci fix format freeze hermeto-clean
.PHONY: hermeto-prefetch konflux-requirements lint lock radon
.PHONY: rpm-lock rpm-lock-container rpm-lockfile-prototype-image setup test typecheck

fix:
	uv run --locked ruff check --fix
	uv run --locked ruff format

lint:
	uv run --locked ruff check src/ tests/ scripts/

format:
	uv run --locked ruff format src/ tests/ scripts/

typecheck:
	uv run --locked ty check src/ scripts/

radon:
	@uv run --locked radon cc src/ -s --min C | grep -q . \
		&& { echo "FAIL: Cyclomatic complexity C or higher detected"; exit 1; } \
		|| echo "PASS: All functions rated A or B"

test:
	uv run --locked pytest

ci: lint format typecheck radon check-konflux-requirements test
	@echo ""
	@echo "✅ All CI checks passed!"

# Regenerate the hermetic build manifests from uv.lock.
konflux-requirements:
	@scripts/konflux_requirements.py

# Fail if the committed manifests have drifted from uv.lock. Run in CI so a
# lock change without a manifest refresh cannot slip through.
check-konflux-requirements:
	@scripts/konflux_requirements.py
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
	@GIT_COMMON=$$(cd "$$(git rev-parse --git-common-dir)" && pwd -P) && \
	$(CONTAINER_RUNTIME) run --rm \
	  -v "$$(pwd):$$(pwd):z" \
	  -v "$$GIT_COMMON:$$GIT_COMMON:z" \
	  -w "$$(pwd)" \
	  $(HERMETO_IMAGE) fetch-deps \
	  --source . --output ./.hermeto-out \
	  '[{"type": "pip", "path": ".", "requirements_files": [".konflux/requirements.txt"], "requirements_build_files": [".konflux/hermeto/build.txt", ".konflux/hermeto/build-pypi.txt"]}, {"type": "rpm", "path": "."}]'

hermeto-clean:
	rm -rf .hermeto-out/

rpm-lockfile-prototype-image:
	@curl -s https://raw.githubusercontent.com/konflux-ci/rpm-lockfile-prototype/refs/tags/v$(RLP_VERSION)/Containerfile \
	| $(CONTAINER_RUNTIME) build -t rpm-lockfile-prototype:$(RLP_VERSION) -

# Regenerate rpms.lock.yaml from rpms.in.yaml against the builder image.
# Resolves the build-toolchain RPM tree for every target arch so Hermeto can
# prefetch them for hermetic builds.
rpm-lock-container: rpm-lockfile-prototype-image
	$(CONTAINER_RUNTIME) run --rm -v "$$(pwd):/work:z" -w /work \
	  $(RLP_IMAGE) --image $(BUILDER) rpms.in.yaml

rpm-lock:
	rpm-lockfile-prototype --image $(BUILDER) rpms.in.yaml

upgrade:
	uv lock --upgrade

freeze: upgrade konflux-requirements
