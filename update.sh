#!/usr/bin/env bash
#
# On-demand refresh of the Helix Tone Search index (the "database" = web/data/*).
#
#   ./update.sh          incremental: crawl from page 1 of each sort and stop when a full
#                        page is already known, enrich only the new tones, embed, and push.
#                        Fast — usually minutes, since new uploads surface at the top.
#   ./update.sh --full   re-crawl every sort field to its ~50-page cap (~75 min at the 10s
#                        crawl-delay), then enrich/embed/push. Use to rebuild from scratch.
#
# Requirements:
#   - python3.12 (the venv is bootstrapped on first run)
#   - a local OpenAI-compatible LLM at http://localhost:11500 for enrichment
#     (adjust ENDPOINT/MODEL in enrich.py / band_enrich.py)
#   - git push rights on this repo (the push triggers the GitHub Pages redeploy)
#
# Run by hand, or schedule it with cron / launchd for periodic refreshes, e.g.:
#   0 8 * * 1  cd /path/to/helix-tone-search && ./update.sh >> /tmp/helix-update.log 2>&1
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/indexer"

FULL=0
[ "${1:-}" = "--full" ] && FULL=1

if [ ! -x .venv/bin/python ]; then
  echo "[update] bootstrapping venv + deps..."
  python3.12 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
PY=.venv/bin/python
SORTS="posted thecount rating name band song guitarist amp style"
# Each sort's listing hard-caps at ~50 pages, so we union both directions: desc
# captures the top ~1,000 tones, asc the bottom ~1,000 — disjoint on a ~10k catalog.
DIRS="desc asc"

echo "[update] 1/4 scrape (union of all sort fields x directions)"
for d in $DIRS; do
  for s in $SORTS; do
    if [ "$FULL" = "1" ]; then
      "$PY" scrape.py --sort "$s" --dir "$d" --max-pages 60 --delay 10 --restart
    else
      "$PY" scrape.py --sort "$s" --dir "$d" --max-pages 60 --delay 10 --restart --stop-on-seen
    fi
  done
done

# Deepen bands we already know via the (uncapped, Helix-scoped) search endpoint.
# Only on --full: it searches ~1k bands at the 10s crawl-delay (hours), too slow to
# run on every incremental refresh.
if [ "$FULL" = "1" ]; then
  echo "[update] 2/4 backfill known bands (Helix-only search endpoint)"
  "$PY" backfill_bands.py --delay 10
else
  echo "[update] 2/4 backfill skipped (only runs on --full)"
fi

echo "[update] 3/4 enrich tags + band metadata in ONE LLM pass (rule fast-path + cached; only new tones hit the LLM)"
# Merged single-pass enricher: ~2x fewer LLM calls than the old enrich.py + band_enrich.py
# two-pass flow, plus a rule-based fast-path that skips the LLM for no-description tones with a
# mappable style. workers=4 matches the single-slot start-gemma-mtp.sh server.
"$PY" enrich_all.py --workers 4

echo "[update] 4/4 embed -> web/data"
"$PY" embed.py --in cache/enriched.jsonl
COUNT=$("$PY" -c "import json;print(json.load(open('../web/data/meta.json'))['count'])")

cd "$ROOT"
if git diff --quiet -- web/data; then
  echo "[update] index unchanged ($COUNT tones) — nothing to deploy"
else
  git add web/data
  git commit -m "Refresh index: $COUNT tones ($(date +%F))"
  git push
  echo "[update] pushed $COUNT tones — GitHub Pages will redeploy"
fi
