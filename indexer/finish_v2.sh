#!/usr/bin/env bash
#
# Post-crawl half of the downloads-fix rebuild: enrich -> embed -> verify.
# Run only after recrawl_v2.sh finishes. Needs the local LLM up at :11500.
#
# Deliberately NOT update.sh: that script defaults enrich_all.py's --in to
# cache/raw.jsonl (the old file whose `downloads` is really the upload year),
# which would rebuild the exact bad index we're replacing. --in is explicit here.
#
# No git push — deploying to the live site is a separate, deliberate step.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PY=.venv/bin/python
RAW=cache/raw_v2.jsonl
ENR=cache/enriched_v2.jsonl

echo "[finish] merge: carry over tones the re-crawl hasn't re-reached yet"
# The backfill is a ~20h job and may be cut short. Tones it hasn't re-reached
# still live in the old catalog — dropping them would shrink the index below
# what's already live. Carry them over, but with downloads=null rather than the
# bogus upload-year: unknown is honest, a wrong number is not. `needs_recrawl`
# marks them so a later backfill pass can top them up.
"$PY" - <<'EOF'
import json
fresh = {}
for l in open('cache/raw_v2.jsonl'):
    if l.strip():
        r = json.loads(l); r['needs_recrawl'] = False; fresh[r['id']] = r
carried = 0
for l in open('cache/raw.jsonl.bak-20260802'):
    if not l.strip():
        continue
    r = json.loads(l)
    if r['id'] in fresh:
        continue
    r['downloads'] = None        # was the upload year — refuse to republish it
    r['needs_recrawl'] = True
    fresh[r['id']] = r; carried += 1
with open('cache/raw_merged.jsonl', 'w') as f:
    for r in fresh.values():
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"[finish] merged {len(fresh)} tones: {len(fresh)-carried} fresh counts, "
      f"{carried} carried with downloads=null")
EOF
RAW=cache/raw_merged.jsonl

echo "[finish] enrich ($(date +%H:%M:%S)) — cache is keyed by id, so only NEW tones hit the LLM"
# workers=3: the MTP server has a single slot and wedges under sustained concurrency.
"$PY" enrich_all.py --in "$RAW" --out "$ENR" --workers 3

# A failed LLM call is cached as {} and the skip check is `id in cache`, so a
# tone that fails once is never retried — a mid-run wedge bakes permanent holes.
# A failed LLM call is cached as {} and never retried, so warning isn't enough:
# purge the empties and re-run, which makes them eligible again. Two rounds —
# a tone that fails twice is a genuine parse/content problem, not a blip.
for round in 1 2; do
  n=$("$PY" - <<'EOF'
import json
p = 'cache/enrich_all_cache.json'
c = json.load(open(p))
empty = [k for k, v in c.items() if not v]
for k in empty:
    del c[k]
json.dump(c, open(p, 'w'))
print(len(empty))
EOF
)
  echo "[finish] poisoned cache entries purged (round $round): $n"
  [ "$n" = "0" ] && break
  echo "[finish] re-running enrichment to retry them"
  "$PY" enrich_all.py --in "$RAW" --out "$ENR" --workers 3
done

echo "[finish] embed ($(date +%H:%M:%S))"
"$PY" embed.py --in "$ENR"

echo "[finish] verify"
"$PY" - <<'EOF'
import json
new = [json.loads(l) for l in open('cache/raw_v2.jsonl') if l.strip()]
old = {json.loads(l)['id']: json.loads(l) for l in open('cache/raw.jsonl.bak-20260802') if l.strip()}
dls = [r['downloads'] for r in new]
bad = sum(1 for r in new if r['date'] and r['downloads'] == int(r['date'][2:4]))
newids = {r['id'] for r in new}
print(f"  tones:            {len(new)}   (was {len(old)})")
print(f"  distinct dl vals: {len(set(dls))}   (was 14)")
print(f"  dl range:         {min(dls)}–{max(dls)}")
print(f"  dl == own year:   {bad}")
print(f"  missing name:     {sum(1 for r in new if not r['name'])}   (was 60)")
print(f"  dropped vs old:   {len(set(old) - newids)}")
print(f"  newly found:      {len(newids - set(old))}")
EOF
echo "[finish] done $(date) — NOT deployed; review, then push web/data to go live"
