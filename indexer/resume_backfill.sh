#!/usr/bin/env bash
#
# Wait out line6.com's soft throttle (HTTP 200 + empty result set, never a 429),
# then resume the band backfill. Probes gently — the point is to stop adding
# load, not to keep poking.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PROBE="https://line6.com/customtone/search/helix/?search_term=metallica"
UA="helix-tone-search/0.1 (personal preset indexer; respects crawl-delay)"

echo "[resume] waiting for throttle to lift — probing every 15m ($(date))"
for i in $(seq 1 32); do   # up to 8h
  n=$(curl -s --max-time 30 -A "$UA" "$PROBE" | grep -o 'class="tone' | wc -l | tr -d ' ')
  echo "[resume] probe $i: $n tone blocks ($(date +%H:%M:%S))"
  if [ "${n:-0}" -gt 0 ]; then
    echo "[resume] endpoint healthy again — resuming backfill"
    exec caffeinate -i .venv/bin/python backfill_bands.py \
        --raw cache/raw_v2.jsonl --enriched cache/enriched.jsonl \
        --state cache/backfill_state_v2.json --delay 10
  fi
  sleep 900
done
echo "[resume] still throttled after 8h — giving up; re-run by hand"
