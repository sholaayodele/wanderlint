.PHONY: install test lint format eval-gate

install:
	uv sync --all-groups

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

# Stub: fails until the eval gate exists (M3), so CI can never pass by accident.
eval-gate:
	@echo "eval-gate: not implemented yet (M3)." >&2
	@exit 1
