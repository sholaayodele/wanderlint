.PHONY: install test lint format eval-gate label label-stats save-labels

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

label:
	uv run --group label streamlit run scripts/label_app.py --server.address localhost

label-stats:
	uv run python scripts/labeling_stats.py

# Commits data/labels/*.jsonl as "Labels: <n>/150 labeled" and pushes.
save-labels:
	./scripts/save_labels.sh
