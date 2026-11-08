#!/usr/bin/env bash
# Commit data/labels/*.jsonl as "Labels: <n>/150 labeled" and push. Usage: make save-labels
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

log=data/labels/labeling_log.jsonl
total=$(grep -c . data/listings/raw/generated.jsonl)
n=0
if [[ -f "$log" ]]; then
  n=$(uv run --no-sync python -c "import json,sys; print(len({json.loads(l)['listing_id'] for l in open(sys.argv[1]) if l.strip()}))" "$log")
fi

git add -- data/labels/*.jsonl
if git diff --cached --quiet -- data/labels/; then
  echo "No label changes to commit."
  exit 0
fi
git commit -m "Labels: ${n}/${total} labeled" -- data/labels/
git push origin HEAD
