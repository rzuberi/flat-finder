#!/bin/zsh
# Daily sweep for every configured city, then publish each site.
cd "$(dirname "$0")"
ROOT="$PWD"

publish() {   # $1 = repo dir
  cd "$1"
  git add -A docs seen.json matches.md 2>/dev/null
  git diff --cached --quiet || git commit -q -m "sweep: $(date +%F)"
  git pull --rebase --quiet
  git push --quiet
  cd "$ROOT"
}

# Brighton (small, first). Site files are shared with the London repo.
if .venv/bin/python apartment_sweep.py --config config/brighton.json; then
  cp docs/index.html docs/app.js docs/style.css /Users/zuberi01/brighton-finder/docs/
  cp state/brighton/seen.json /Users/zuberi01/brighton-finder/seen.json
  cp state/brighton/matches.md /Users/zuberi01/brighton-finder/matches.md
  publish /Users/zuberi01/brighton-finder || echo "brighton publish failed"
else
  echo "brighton sweep failed"
fi

# London
if .venv/bin/python apartment_sweep.py --config config/london.json; then
  publish "$ROOT" || echo "london publish failed"
else
  echo "london sweep failed"
fi
