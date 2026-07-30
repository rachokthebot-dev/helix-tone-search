"""Merged enrichment: tags + band metadata in ONE LLM call per tone.

Replaces the two-pass enrich.py(--tags-only) + band_enrich.py for future runs. Both
old passes took the same inputs and hit the LLM separately; folding them into one
prompt halves the round-trips (~2x fewer calls) with no loss of the safety rules:
  - band stays the uploader's field; band_inferred/song_inferred only fill BLANKS,
    stored separately (attribution is never overwritten).
  - genre_tags/tone_tags are pure classification and never touch band/song.

Fast-path (skips the LLM entirely): a tone with NO description whose `style` maps
confidently to tags gets rule-based tags + a deterministic band_norm — the LLM adds
nothing there (no description => no gear/mentions to extract, and the style->tags
mapping just codifies what the prompt already hardcodes as examples). When the style
is messy/unknown, we fall back to the LLM, so quality never degrades.

Cached by id in cache/enrich_all_cache.json; concurrent, resumable.
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

SYSTEM = (
    "You extract structured metadata for Line 6 Helix guitar presets ('tones'). "
    "You return ONLY one valid JSON object — no prose, no code fences. "
    "Never invent facts: use null or [] when a field is not clearly supported."
)

USER_TMPL = """Preset fields (some may be blank, messy, or list several bands):
name: {name}
band: {band}
song: {song}
guitarist: {guitarist}
amp: {amp}
style: {style}
description: {description}

Return JSON with EXACTLY these keys:
{{
  "genre_tags": [string],        // up to 4 lowercase genres (e.g. "metal","blues","ambient","funk")
  "tone_tags": [string],         // 2-5 short tone descriptors ("high-gain","clean","delay","fuzz","lead","crunch")
  "band_norm": string or null,   // the GIVEN band in canonical form: fix casing/spelling, expand abbreviations
                                 // (GNR->Guns N' Roses, ACDC->AC/DC, RHCP->Red Hot Chili Peppers). null if band
                                 // is blank/"any"/"none" or not a real band.
  "bands": [string],             // if the band field names several bands (split on , / & +), each canonical; else []
  "aliases": [string],           // extra search terms: band nicknames/abbreviations, song shorthand. [] if none.
  "band_inferred": string or null,  // ONLY if band is blank: a band clearly implied by name/description. Conservative;
                                 // null unless confident. NEVER infer from gear/amp/pickup terms.
  "song_inferred": string or null,  // ONLY if song is blank: a song clearly implied by name/description.
  "mentioned_bands": [string],   // real bands/artists referenced in name/description beyond the band field. [] if none.
  "mentioned_songs": [string],   // songs referenced in name/description. [] if none.
  "gear": [string],              // specific amp/cab/pedal MODELS named in the description ("Marshall JCM800","Klon
                                 // Centaur","Strymon Timeline"). Omit generic words like "amp"/"delay".
  "features": [string]           // signal-chain features present: "snapshots","looper","compressor","IR","wah",
                                 // "pitch shifter","expression pedal","bass","acoustic". [] if none.
}}

Always give at least 2 tone_tags, inferring from style/genre/name when there is no description
("Metal" implies "high-gain","distortion"; "Ambient" implies "clean","reverb","delay"). Only
band/song/artist fields stay null when genuinely unknown. Include only facts supported by the text."""

# --- fast-path rule tables (mirror the prompt's own hardcoded examples) ---
# style keyword -> (genre_tags, tone_tags). First match wins; order = most specific first.
STYLE_RULES = [
    (("metal", "djent", "thrash", "death", "grind", "doom", "core", "black"),
     (["metal"], ["high-gain", "distortion"])),
    (("blues",), (["blues"], ["overdrive", "crunch"])),
    (("jazz", "fusion"), (["jazz"], ["clean", "warm"])),
    (("ambient", "post", "shoegaze", "atmospher"), (["ambient"], ["clean", "reverb", "delay"])),
    (("acoustic", "folk", "country", "worship", "praise", "gospel"),
     (["acoustic"], ["clean", "acoustic"])),
    (("funk", "soul", "r&b", "disco"), (["funk"], ["clean", "funk"])),
    (("punk",), (["punk"], ["distortion", "crunch"])),
    (("rock", "grunge", "alternative", "indie"), (["rock"], ["crunch", "overdrive"])),
    (("clean",), ([], ["clean"])),
    (("lead", "shred"), ([], ["lead", "high-gain"])),
]

ABBREV = {
    "gnr": "Guns N' Roses", "acdc": "AC/DC", "ac/dc": "AC/DC", "ac-dc": "AC/DC",
    "rhcp": "Red Hot Chili Peppers", "ratm": "Rage Against the Machine",
    "smp": "Smashing Pumpkins", "gnfr": "Guns N' Roses", "srv": "Stevie Ray Vaughan",
}


def rule_tags(style: str):
    """Confident (genre_tags, tone_tags) from a style string, or None if not mappable."""
    s = (style or "").lower()
    if not s.strip():
        return None
    for kws, tags in STYLE_RULES:
        if any(k in s for k in kws):
            return tags
    return None


def normalize_band(band: str):
    b = (band or "").strip()
    if not b or b.lower() in ("any", "none", "n/a"):
        return None
    if b.lower() in ABBREV:
        return ABBREV[b.lower()]
    if b.islower() or b.isupper():      # only re-case obviously-broken casing; leave mixed as-is
        return b.title()
    return b


def call_llm(rec: dict, timeout: float) -> dict:
    user = USER_TMPL.format(
        name=rec.get("name") or "", band=rec.get("band") or "", song=rec.get("song") or "",
        guitarist=rec.get("guitarist") or "", amp=rec.get("amp") or "",
        style=rec.get("style") or "", description=(rec.get("description") or "")[:800])
    payload = {"model": MODEL, "temperature": 0.2,
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    m = JSON_RE.search(body["choices"][0]["message"]["content"])
    if not m:
        raise ValueError("no JSON in response")
    return json.loads(m.group(0))


def cstr(v):
    return v.strip() if isinstance(v, str) and v.strip() and v.strip().lower() not in ("any", "none", "n/a") else None


def clist(v, n=8):
    return [x.strip() for x in v if isinstance(x, str) and x.strip()][:n] if isinstance(v, list) else []


def clean_tags(v, n):
    return [x.strip().lower() for x in v if isinstance(x, str) and x.strip()][:n] if isinstance(v, list) else []


def rule_record(rec: dict) -> dict:
    """Fast-path result for a no-description tone with a mappable style — no LLM."""
    g, t = rule_tags(rec.get("style"))
    return {"genre_tags": g, "tone_tags": t, "band_norm": normalize_band(rec.get("band")),
            "bands": [], "aliases": [], "band_inferred": None, "song_inferred": None,
            "mentioned_bands": [], "mentioned_songs": [], "gear": [], "features": [], "_src": "rule"}


def build_record(rec: dict, llm: dict) -> dict:
    out = dict(rec)
    out["genre_tags"] = clean_tags(llm.get("genre_tags"), 4)
    out["tone_tags"] = clean_tags(llm.get("tone_tags"), 5)
    out["artist"] = rec.get("guitarist") or None
    out["band_norm"] = cstr(llm.get("band_norm"))
    out["bands"] = clist(llm.get("bands"))
    out["aliases"] = clist(llm.get("aliases"))
    out["band_inferred"] = cstr(llm.get("band_inferred")) if not rec.get("band") else None
    out["song_inferred"] = cstr(llm.get("song_inferred")) if not rec.get("song") else None
    out["mentioned_bands"] = clist(llm.get("mentioned_bands"))
    out["mentioned_songs"] = clist(llm.get("mentioned_songs"))
    out["gear"] = clist(llm.get("gear"), 8)
    out["features"] = clist(llm.get("features"), 8)
    out["enrich_source"] = "field" if rec.get("band") else ("llm-inferred" if out["band_inferred"] else "none")
    return out


def eligible_rule_path(rec: dict) -> bool:
    """No description to mine AND a confidently-mappable style => the LLM adds nothing."""
    return not (rec.get("description") or "").strip() and rule_tags(rec.get("style")) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="cache/raw.jsonl")
    ap.add_argument("--out", default="cache/enriched.jsonl")
    ap.add_argument("--cache", default="cache/enrich_all_cache.json")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--workers", type=int, default=4, help="concurrent LLM requests (match server slots)")
    ap.add_argument("--no-fast-path", action="store_true", help="disable the rule-based skip (LLM every tone)")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.inp).read_text().split("\n") if l.strip()]
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    # split work: cached (skip), rule-path (cheap, no LLM), llm (the expensive set)
    todo, ruled = [], 0
    for r in rows:
        if r["id"] in cache:
            continue
        if not args.no_fast_path and eligible_rule_path(r):
            cache[r["id"]] = rule_record(r)
            ruled += 1
        else:
            todo.append(r)
    print(f"[enrich_all] {len(rows)} tones | {ruled} via rule fast-path (no LLM) | "
          f"{len(todo)} need the LLM (workers={args.workers})", file=sys.stderr)

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
                        print(f"[enrich_all] {done}/{len(todo)} llm (ok={ok} fail={fail})", file=sys.stderr)

    cache_path.write_text(json.dumps(cache))
    enriched = [build_record(r, cache.get(r["id"], {})) for r in rows]
    with Path(args.out).open("w") as f:
        for r in enriched:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tagged = sum(1 for r in enriched if r["genre_tags"] or r["tone_tags"])
    print(f"[enrich_all] wrote {len(enriched)} to {args.out}; {tagged} tagged, "
          f"{ruled} rule-path, {ok} llm-ok, {fail} llm-fail", file=sys.stderr)


if __name__ == "__main__":
    main()
