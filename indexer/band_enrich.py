"""Band pass: make band/song search reliable.

On top of the tag enrichment, this normalizes band/song metadata with the local LLM:
  band_norm     canonical form of the existing band (casing/spelling/abbreviation)
  bands[]       split list when one tone credits several bands
  aliases[]     extra search terms (band nicknames, "CAYA" -> "Come As You Are")
  band_inferred only when the band field is blank AND the name/description clearly implies one
  song_inferred likewise for song

Normalization (band_norm/bands/aliases) is low-risk — it reshapes data that's already
there. Inference (band_inferred/song_inferred) is conservative and only fills blanks, so
the original uploader fields are never overwritten. Concurrent, cached, resumable.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import threading
import urllib.request
from pathlib import Path

ENDPOINT = "http://localhost:11500/v1/chat/completions"
MODEL = "gemma-hermes:latest"
JSON_RE = re.compile(r"\{.*\}", re.S)

SYSTEM = ("You normalize band and song metadata for Line 6 Helix guitar presets. "
          "You return ONLY one valid JSON object — no prose, no code fences.")

USER_TMPL = """Preset fields (band/song may be blank, messy, or list several bands):
band: {band}
song: {song}
guitarist: {guitarist}
name: {name}
description: {description}

Return JSON with exactly these keys:
{{
  "band_norm": string or null,      // the given band in canonical form: fix capitalization/spelling and
                                    // expand abbreviations (GNR->Guns N' Roses, ACDC->AC/DC, RHCP->Red Hot
                                    // Chili Peppers, RATM->Rage Against the Machine). null if band is blank,
                                    // "any", "none", or not a real band.
  "bands": [string],                // if the band field names several bands (split on , / & +), each canonical;
                                    // otherwise [] or the single canonical name.
  "aliases": [string],              // extra search terms: band nicknames/abbreviations and common song-title
                                    // shorthand (e.g. "CAYA" for "Come As You Are"). [] if none.
  "band_inferred": string or null,  // ONLY if band is blank: a band clearly implied by name/description. Be
                                    // conservative; null unless confident. NEVER infer from gear/amp/pickup terms.
  "song_inferred": string or null   // ONLY if song is blank: a song clearly implied by name/description. null unless confident.
}}"""


def call_llm(rec: dict, timeout: float) -> dict:
    user = USER_TMPL.format(
        band=rec.get("band") or "", song=rec.get("song") or "",
        guitarist=rec.get("guitarist") or "", name=rec.get("name") or "",
        description=(rec.get("description") or "")[:500])
    payload = {"model": MODEL, "temperature": 0.1,
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    m = JSON_RE.search(body["choices"][0]["message"]["content"])
    if not m:
        raise ValueError("no JSON")
    return json.loads(m.group(0))


def cstr(v):
    return v.strip() if isinstance(v, str) and v.strip() and v.strip().lower() not in ("any", "none", "n/a") else None


def clist(v, n=8):
    return [x.strip() for x in v if isinstance(x, str) and x.strip()][:n] if isinstance(v, list) else []


def build_record(rec: dict, llm: dict) -> dict:
    out = dict(rec)
    out["band_norm"] = cstr(llm.get("band_norm"))
    out["bands"] = clist(llm.get("bands"))
    out["aliases"] = clist(llm.get("aliases"))
    # inference only fills blanks — never overwrite an uploader value
    out["band_inferred"] = cstr(llm.get("band_inferred")) if not rec.get("band") else None
    out["song_inferred"] = cstr(llm.get("song_inferred")) if not rec.get("song") else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="cache/enriched.jsonl")
    ap.add_argument("--out", default="cache/enriched.jsonl")
    ap.add_argument("--cache", default="cache/band_cache.json")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--max-new", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.inp).read_text().split("\n") if l.strip()]
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    todo = [r for r in rows if r["id"] not in cache]
    if args.max_new:
        todo = todo[:args.max_new]
    print(f"[band] {len(cache)} cached, {len(todo)} to process (workers={args.workers})", file=sys.stderr)

    ok = fail = done = 0
    lock = threading.Lock()

    def work(rec):
        try:
            return rec["id"], call_llm(rec, args.timeout), True
        except Exception:
            return rec["id"], {}, False

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for fut in concurrent.futures.as_completed([ex.submit(work, r) for r in todo]):
                tid, llm, okk = fut.result()
                with lock:
                    cache[tid] = llm
                    done += 1; ok += int(okk); fail += int(not okk)
                    if done % 25 == 0:
                        cache_path.write_text(json.dumps(cache))
                        print(f"[band] {done}/{len(todo)} (ok={ok} fail={fail}) cached={len(cache)}", file=sys.stderr)

    cache_path.write_text(json.dumps(cache))
    enriched = [build_record(rec, cache.get(rec["id"], {})) for rec in rows]
    with Path(args.out).open("w") as f:
        for r in enriched:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    norm = sum(1 for r in enriched if r.get("band_norm"))
    inf = sum(1 for r in enriched if r.get("band_inferred"))
    pending = sum(1 for r in rows if r["id"] not in cache)
    print(f"[band] wrote {len(enriched)} to {args.out}; {norm} normalized, {inf} band-inferred, "
          f"{pending} pending (ok={ok} fail={fail})", file=sys.stderr)


if __name__ == "__main__":
    main()
