.PHONY: lint format typecheck radon test ci setup

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
	uv run pytest -v --cov=okp_mcp --cov-report=term-missing

ci: lint typecheck radon test

setup:
	uv sync --group dev
	pre-commit install
