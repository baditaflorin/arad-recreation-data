#!/usr/bin/env bash
# Publish a reviewed checkout; requires an existing authenticated GitHub CLI.
set -euo pipefail
cd "$(dirname "$0")/.."
repo="baditaflorin/arad-recreation-data"
gh auth status >/dev/null
git rev-parse --show-toplevel >/dev/null
origin="$(git remote get-url origin)"
if [[ "$origin" != "https://github.com/$repo.git" && "$origin" != "git@github.com:$repo.git" ]]; then
  printf 'Unexpected Git remote; review the destination before publishing.\n' >&2
  exit 1
fi
python scripts/scrape_recons.py --offline
python scripts/build_site.py
python scripts/validate_exports.py
python -m unittest discover -s tests -v
git add README.md PAGES.md DATA_LICENSE.md LICENSE requirements.txt assets data docs examples raw scripts tests .gitignore
if ! git diff --cached --quiet; then git commit -m 'Update Recons inventory and download portal'; fi
git push origin main
if [[ "${1:-}" == "--pages" ]]; then
  if ! gh api "repos/$repo/pages" >/dev/null 2>&1; then
    gh api --method POST "repos/$repo/pages" --input - <<'JSON'
{"build_type":"legacy","source":{"branch":"main","path":"/docs"}}
JSON
  fi
  gh api "repos/$repo/pages" --jq .html_url
fi
