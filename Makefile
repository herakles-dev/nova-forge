.PHONY: check lint format test syntax all

# Run all checks (lint + syntax + tests)
all: check test

# Quick validation: lint + syntax check on all Python files
check: lint syntax

# Ruff lint (errors + warnings, no auto-fix)
lint:
	@echo "=== Ruff Lint ==="
	ruff check . --no-fix

# Ruff format check (reports unformatted files, doesn't modify)
format-check:
	@echo "=== Ruff Format Check ==="
	ruff format --check .

# Auto-fix: lint + format
fix:
	ruff check . --fix
	ruff format .

# Syntax check all root Python files
syntax:
	@echo "=== Syntax Check ==="
	@for f in *.py; do \
		python3 -c "import py_compile; py_compile.compile('$$f', doraise=True)" && echo "  $$f OK" || exit 1; \
	done
	@echo "All syntax checks passed"

# Run full test suite
test:
	python3 -m pytest tests/ -x -q

# Run tests with coverage
test-cov:
	python3 -m pytest tests/ -x -q --tb=short --co -q 2>/dev/null | tail -1
