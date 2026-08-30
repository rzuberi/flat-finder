#!/bin/zsh
# Daily flat sweep: refresh listing data, then publish.
set -e
cd "$(dirname "$0")"
.venv/bin/python apartment_sweep.py
git add docs/data.json seen.json matches.md
git diff --cached --quiet || git commit -q -m "sweep: $(date +%F)"
git pull --rebase --quiet   # pick up any edits pushed from elsewhere
git push --quiet
