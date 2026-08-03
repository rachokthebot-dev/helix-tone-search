#!/usr/bin/env bash
#
# Full re-crawl into a FRESH cache/raw_v2.jsonl, to replace the catalog whose
# `downloads` column is really the upload year (see vault: helix-tone-search).
#
# Deliberately writes to a new file: re-crawling in place would leave any tone
# the crawl doesn't re-reach holding its old bogus count, silently mixing good
# and bad rows. A separate file makes "was this row refreshed?" answerable.
#
# Crawl only — no enrichment (needs the local LLM), no embed, no push.
set -uo pipefail   # NOT -e: one failed window must not kill an 8h overnight run

cd "$(dirname "${BASH_SOURCE[0]}")"
PY=.venv/bin/python
OUT=cache/raw_v2.jsonl
SORTS="posted thecount rating name band song guitarist amp style"
DIRS="desc asc"

echo "[recrawl] start $(date)"

for d in $DIRS; do
  for s in $SORTS; do
    echo "[recrawl] === sort=$s dir=$d ($(date +%H:%M:%S)) ==="
    "$PY" scrape.py --out "$OUT" --sort "$s" --dir "$d" \
                    --max-pages 60 --delay 10 --restart \
      || echo "[recrawl] WARN: window $s/$d exited $? — continuing"
  done
done

echo "[recrawl] === band backfill ($(date +%H:%M:%S)) ==="
# Uncapped Helix-scoped search endpoint; reaches artists' old/low-download tones
# that the 50-page-per-sort listing cap hides. This is the long pole (hours).
"$PY" backfill_bands.py --raw "$OUT" --enriched cache/enriched.jsonl \
                        --state cache/backfill_state_v2.json --delay 10 --restart \
  || echo "[recrawl] WARN: backfill exited $? — continuing"

echo "[recrawl] done $(date)"
"$PY" - <<'EOF'
import json, collections
recs = [json.loads(l) for l in open('cache/raw_v2.jsonl') if l.strip()]
dls = [r['downloads'] for r in recs]
bad = sum(1 for r in recs if r['date'] and r['downloads'] == int(r['date'][2:4]))
print(f"[recrawl] {len(recs)} tones | {len(set(dls))} distinct download values "
      f"| max={max(dls)} | downloads==own-year: {bad}")
EOF
