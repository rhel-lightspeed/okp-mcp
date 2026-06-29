.PHONY: fix lint format typecheck radon test ci setup konflux-requirements check-konflux-requirements

fix:
	pdm run ruff check --fix
	pdm run ruff format

lint:
	pdm run ruff check src/ tests/

format:
	pdm run ruff format src/ tests/

typecheck:
	pdm run ty check src/

radon:
	@pdm run radon cc src/ -s --min C | grep -q . \
		&& { echo "FAIL: Cyclomatic complexity C or higher detected"; exit 1; } \
		|| echo "PASS: All functions rated A or B"

test:
	pdm run pytest

ci: lint typecheck radon check-konflux-requirements test
	@echo ""
	@echo "✅ All CI checks passed!"

# Regenerate the hermetic build manifests from pdm.lock.
konflux-requirements:
	./scripts/konflux_requirements.sh

# Fail if the committed manifests have drifted from pdm.lock. Run in CI so a
# lock change without a manifest refresh cannot slip through.
check-konflux-requirements:
	./scripts/konflux_requirements.sh
	@test -z "$$(git status --porcelain -- .konflux/)" \
		|| { echo "FAIL: .konflux manifests are stale. Run 'make konflux-requirements' and commit."; git status --porcelain -- .konflux/; exit 1; }

setup:
	pdm install --group dev
	pre-commit install
