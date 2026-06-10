.PHONY: fix lint format typecheck radon test ci setup

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

ci: lint typecheck radon test
	@echo ""
	@echo "✅ All CI checks passed!"

setup:
	uv sync --locked --group dev
	pre-commit install
