#!/usr/bin/env bash
#
# Rebuild + deploy the index every N bands of backfill progress, then once more
# when the backfill finishes.
#
# The local LLM is started per cycle and stopped again straight after: a
# resident 26B holds ~12 GB wired, and this loop spans ~14h of crawling.
# Enrichment is id-cached, so each cycle only pays for genuinely new tones.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

STEP=${1:-100}
LOG=/tmp/helix-resume.log
MODEL=~/.claude/skills/local-model/scripts/local-model.sh
last=0

bands() { grep -oE '^\[backfill\] [0-9]+/' "$LOG" | tail -1 | grep -oE '[0-9]+'; }

refresh() {
  local n="$1"
  echo "[refresh] === cycle at $n bands ($(date +%H:%M:%S)) ==="

  # Only pay for a model load if there is actually LLM work to do.
  local need
  need=$(.venv/bin/python - <<'EOF'
import json
cache = json.load(open('cache/enrich_all_cache.json'))
rows = [json.loads(l) for l in open('cache/raw_v2.jsonl') if l.strip()]
print(sum(1 for r in rows if r['id'] not in cache))
EOF
)
  echo "[refresh] $need tones need enrichment"

  local started=0
  if [ "${need:-0}" -gt 0 ]; then
    echo "[refresh] starting local model"
    "$MODEL" start gemma >/dev/null 2>&1 && started=1
    [ "$started" = "1" ] || { echo "[refresh] model failed to start — skipping cycle"; return 1; }
  fi

  ./finish_v2.sh 2>&1 | sed 's/^/[refresh]   /'
  local rc=${PIPESTATUS[0]}

  if [ "$started" = "1" ]; then
    echo "[refresh] stopping local model"
    "$MODEL" stop >/dev/null 2>&1
  fi
  [ "$rc" = "0" ] || { echo "[refresh] finish_v2 failed (rc=$rc) — not deploying"; return 1; }

  cd ..
  if git diff --quiet -- web/data; then
    echo "[refresh] index unchanged — nothing to deploy"
  else
    local count
    count=$(.venv/bin/python -c "import json;print(json.load(open('web/data/meta.json'))['count'])" 2>/dev/null \
            || indexer/.venv/bin/python -c "import json;print(json.load(open('web/data/meta.json'))['count'])")
    git add web/data
    git commit -q -m "Refresh index: $count tones, $n/2338 bands backfilled

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NfAP1tmmsAPvSfEpZYbVxY"
    git push -q && echo "[refresh] deployed $count tones"
  fi
  cd indexer
}

echo "[refresh] loop armed — every $STEP bands, plus a final pass at completion"
last=$(bands); last=${last:-0}
echo "[refresh] starting from $last bands"

while pgrep -f backfill_bands.py >/dev/null; do
  sleep 300
  cur=$(bands); cur=${cur:-0}
  if [ "$cur" -ge $((last + STEP)) ]; then
    refresh "$cur" && last=$cur
  fi
done

echo "[refresh] backfill finished — final refresh"
refresh "$(bands)"
echo "[refresh] loop done $(date)"
