"""Enrich raw tones with a local LLM (Gemma via the ollama-config proxy on :11500).

For each tone we ask the model to (a) fill band/song when the uploader left them
blank but the name/description imply them, and (b) add genre_tags + tone_tags for
better semantic recall. Results are cached by id so re-runs are near-free.

Never invents facts: the prompt tells the model to return null / empty when unsure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ENDPOINT = "http://localhost:11500/v1/chat/completions"
MODEL = "gemma-hermes:latest"

SYSTEM = (
    "You extract structured metadata for Line 6 Helix guitar presets ('tones'). "
    "You return ONLY a single valid JSON object, no prose, no code fences. "
    "Never invent facts: if a field is not clearly implied by the input, use null "
    "or an empty array."
)

USER_TMPL = """Preset fields (some may be blank or messy):
name: {name}
band: {band}
song: {song}
guitarist: {guitarist}
amp: {amp}
style: {style}
description: {description}

Return JSON with exactly these keys:
{{
  "band": string or null,        // normalized proper band/artist name the tone emulates
  "song": string or null,        // song title if implied
  "artist": string or null,      // guitarist / player name
  "genre_tags": [string],        // up to 4 lowercase genres, e.g. "metal","blues","ambient"
  "tone_tags": [string]          // up to 5 short tone descriptors, e.g. "high-gain","clean","delay","fuzz","lead"
}}

Always provide at least 2 tone_tags, inferring from the style/genre/name when there is
no description (e.g. a "Metal" style implies "high-gain","distortion"; "Ambient" implies
"clean","reverb","delay"). Only band/song/artist must stay null when genuinely unknown."""

TAGS_USER_TMPL = """Guitar preset (Line 6 Helix). Classify it — do NOT identify the band or song.
name: {name}
band: {band}
song: {song}
style: {style}
description: {description}

Return JSON with exactly these keys:
{{
  "genre_tags": [string],        // up to 4 lowercase genres, e.g. "metal","blues","ambient","funk"
  "tone_tags": [string]          // 2-5 short tone descriptors, e.g. "high-gain","clean","delay","fuzz","lead","crunch"
}}

Always provide at least 2 tone_tags, inferring from style/genre/name when there is no
description (e.g. "Metal" implies "high-gain","distortion"; "Ambient" implies "clean","reverb","delay")."""

JSON_RE = re.compile(r"\{.*\}", re.S)


def call_llm(rec: dict, timeout: float, tags_only: bool = False) -> dict:
    if tags_only:
        user = TAGS_USER_TMPL.format(
            name=rec.get("name") or "", band=rec.get("band") or "",
            song=rec.get("song") or "", style=rec.get("style") or "",
            description=(rec.get("description") or "")[:800])
    else:
        user = USER_TMPL.format(
            name=rec.get("name") or "", band=rec.get("band") or "",
            song=rec.get("song") or "", guitarist=rec.get("guitarist") or "",
            amp=rec.get("amp") or "", style=rec.get("style") or "",
            description=(rec.get("description") or "")[:800])
    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    content = body["choices"][0]["message"]["content"]
    m = JSON_RE.search(content)
    if not m:
        raise ValueError(f"no JSON in response: {content[:120]}")
    return json.loads(m.group(0))


def clean_list(v, n):
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip().lower())
    return out[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="cache/raw.jsonl")
    ap.add_argument("--out", default="cache/enriched.jsonl")
    ap.add_argument("--cache", default="cache/enrich_cache.json")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--tags-only", action="store_true",
                    help="only generate genre/tone tags; never let the LLM guess band/song")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.inp).read_text().split("\n") if l.strip()]
    if args.limit:
        rows = rows[:args.limit]

    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    enriched = []
    ok = fail = 0
    for i, rec in enumerate(rows, 1):
        tid = rec["id"]
        if tid in cache:
            llm = cache[tid]
        else:
            try:
                llm = call_llm(rec, args.timeout, tags_only=args.tags_only)
                ok += 1
            except Exception as e:
                print(f"[enrich] {tid} failed: {e}", file=sys.stderr)
                llm = {}
                fail += 1
            cache[tid] = llm
            if i % 10 == 0:
                cache_path.write_text(json.dumps(cache))
                print(f"[enrich] {i}/{len(rows)} (ok={ok} fail={fail})", file=sys.stderr)

        had_band = bool(rec.get("band"))
        band = rec.get("band") or (llm.get("band") if isinstance(llm.get("band"), str) else None)
        song = rec.get("song") or (llm.get("song") if isinstance(llm.get("song"), str) else None)
        artist = rec.get("guitarist") or (llm.get("artist") if isinstance(llm.get("artist"), str) else None)
        out = dict(rec)
        out.update({
            "band": band, "song": song, "artist": artist,
            "genre_tags": clean_list(llm.get("genre_tags"), 4),
            "tone_tags": clean_list(llm.get("tone_tags"), 5),
            "enrich_source": "field" if had_band else ("llm" if band else "none"),
        })
        enriched.append(out)

    cache_path.write_text(json.dumps(cache))
    with Path(args.out).open("w") as f:
        for r in enriched:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[enrich] wrote {len(enriched)} to {args.out} (llm ok={ok} fail={fail})", file=sys.stderr)


if __name__ == "__main__":
    main()
