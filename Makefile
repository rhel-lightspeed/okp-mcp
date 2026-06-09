.PHONY: lint format typecheck radon test ci setup konflux-requirements check-konflux-requirements

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run ty check src/

radon:
	@uv run radon cc src/ -s --min C | grep -q . \
		&& { echo "FAIL: Cyclomatic complexity C or higher detected"; exit 1; } \
		|| echo "PASS: All functions rated A or B"

test:
	uv run pytest

ci: lint typecheck radon check-konflux-requirements test

# Regenerate the hermetic build manifests from uv.lock.
konflux-requirements:
	./scripts/konflux_requirements.sh

# Fail if the committed manifests have drifted from uv.lock. Run in CI so a
# lock change without a manifest refresh cannot slip through.
check-konflux-requirements:
	./scripts/konflux_requirements.sh
	@test -z "$$(git status --porcelain -- .konflux/)" \
		|| { echo "FAIL: .konflux manifests are stale. Run 'make konflux-requirements' and commit."; git status --porcelain -- .konflux/; exit 1; }

setup:
	uv sync --group dev
	pre-commit install
