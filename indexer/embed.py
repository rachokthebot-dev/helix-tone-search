"""Embed enriched tones and emit the static web artifacts.

Model parity is critical: this uses all-MiniLM-L6-v2 (384-d) via fastembed/ONNX,
the same weights transformers.js loads in the browser (Xenova/all-MiniLM-L6-v2).
Embeddings are L2-normalized then int8-quantized so the browser does cosine as a
plain dot product over `vectors.bin`.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BROWSER_MODEL = "Xenova/all-MiniLM-L6-v2"  # transformers.js equivalent
DIMS = 384

DISPLAY_FIELDS = ["id", "name", "author", "band", "song", "artist", "amp",
                  "style", "device", "downloads", "date", "genre_tags",
                  "tone_tags", "enrich_source", "url",
                  "band_norm", "bands", "aliases", "band_inferred", "song_inferred",
                  "mentioned_bands", "mentioned_songs", "gear", "features", "needs_ir", "snapshots"]

# A preset built around third-party impulse responses sounds wrong without them,
# so it's filtered out by default in the UI. Derived here rather than published as
# raw text: the description itself is deliberately never shipped.
IR_WANTED = re.compile(
    r"\bIRs?\b.{0,60}(download|here|link|drive|dropbox|included|need|require|purchase|buy)"
    r"|\b(ownhammer|york audio|celestion|valhallir|3sigma|redwirez|ggd|tone shepherd)\b", re.I)
# ...unless the author bundled them or stayed on stock cabs.
IR_SELF_CONTAINED = re.compile(
    r"\bIRs?\b.{0,40}(included|attached|in the zip|comes with)|stock cab|no ir\b|without ir|factory cab", re.I)


# Snapshots are the Helix feature players most want to know about up front, but the
# count only exists where the author wrote it down. `0` means "uses snapshots, count
# unknown" — distinct from the field being absent, which means no snapshots at all.
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8}
SNAP_COUNT = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight)\s*[-\s]?\s*snapshots?\b", re.I)


def snapshot_count(rec: dict) -> int | None:
    tagged = any("snapshot" in (f or "").lower() for f in (rec.get("features") or []))
    m = SNAP_COUNT.search(rec.get("description") or "")
    if m:
        tok = m.group(1).lower()
        n = WORD_NUM.get(tok) or (int(tok) if tok.isdigit() else 0)
        if 1 <= n <= 8:  # the hardware has exactly 8 slots; anything else is a misparse
            return n
    return 0 if tagged else None


def needs_ir(rec: dict) -> bool:
    desc = rec.get("description") or ""
    if IR_SELF_CONTAINED.search(desc):
        return False
    tagged = any((f or "").strip().lower() == "ir" for f in (rec.get("features") or []))
    return tagged or bool(IR_WANTED.search(desc))


# Uploaders type whatever they like into band/song/guitarist. Placeholders ("N/A",
# "any") render as fake attributions, and prose ("It'd work for John Mayer, SRV,
# AC/DC…") makes a general-purpose preset match every band it name-drops as though
# it were that band's tone. A comma-separated list of names is NOT prose — those are
# genuine multi-band presets and must survive.
PLACEHOLDER = re.compile(r"^(n/?a|na|any|none|various|test|all|whatever|idk|-+|\?+|\.+|x+)$", re.I)
PROSE = re.compile(r"\b(it'?d|would|works?|working|use[ds]?|using|anything|et\s*al|"
                   r"great for|good for|perfect for|sounds? like|my |your |you )\b", re.I)


def clean_attr(v):
    if not v:
        return None
    s = " ".join(str(v).split())
    if not s or PLACEHOLDER.match(s):
        return None
    if len(s.split()) > 4 and PROSE.search(s):
        return None
    return s or None


def clean_record(rec: dict) -> None:
    for f in ("band", "song", "artist", "guitarist"):
        rec[f] = clean_attr(rec.get(f))
    b, s = rec.get("band"), rec.get("song")
    if b and s and b.strip().lower() == s.strip().lower():
        rec["song"] = None  # "Bass — Bass" is noise, not a song credit


def compose(rec: dict) -> str:
    parts = [
        rec.get("name") or "",
        " ".join(filter(None, [rec.get("band"), rec.get("song"), rec.get("artist")])),
        " ".join(filter(None, [rec.get("band_norm"), rec.get("band_inferred"), rec.get("song_inferred")]
                        + (rec.get("bands") or []) + (rec.get("aliases") or [])
                        + (rec.get("mentioned_bands") or []) + (rec.get("mentioned_songs") or []))),
        " ".join((rec.get("gear") or []) + (rec.get("features") or [])),
        rec.get("style") or "",
        " ".join(rec.get("genre_tags") or []),
        " ".join(rec.get("tone_tags") or []),
        rec.get("amp") or "",
        (rec.get("description") or "")[:600],
    ]
    return ". ".join(p for p in parts if p.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="cache/enriched.jsonl")
    ap.add_argument("--outdir", default="../web/data")
    args = ap.parse_args()

    inp = Path(args.inp)
    if not inp.exists():
        inp = Path("cache/raw.jsonl")
    rows = [json.loads(l) for l in inp.read_text().split("\n") if l.strip()]
    print(f"[embed] {len(rows)} tones from {inp}")

    for r in rows:
        clean_record(r)  # before compose(): the embedding should see clean text too

    texts = [compose(r) for r in rows]
    model = TextEmbedding(model_name=MODEL)
    vecs = np.array(list(model.embed(texts)), dtype=np.float32)
    # L2-normalize so dot product == cosine similarity
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.clip(norms, 1e-9, None)
    # int8 quantize (values in [-1,1] -> [-127,127])
    q = np.clip(np.round(vecs * 127), -127, 127).astype(np.int8)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "vectors.bin").write_bytes(q.tobytes())

    # Verbatim descriptions (user-authored text) are intentionally NOT published — the
    # useful signal is extracted into structured fields above and kept in the embedding.
    for r in rows:
        r["needs_ir"] = needs_ir(r)
        r["snapshots"] = snapshot_count(r)
    presets = [{k: r.get(k) for k in DISPLAY_FIELDS} for r in rows]
    (outdir / "presets.json").write_text(json.dumps(presets, ensure_ascii=False))

    built = datetime.now(timezone.utc)
    # Download counts land progressively as the band backfill advances, so publish
    # the coverage alongside the build stamp — "how complete is this index" is the
    # question a version number alone can't answer.
    known = sum(1 for r in rows if r.get("downloads") is not None)
    meta = {
        "model": MODEL,
        "browser_model": BROWSER_MODEL,
        "dims": DIMS,
        "count": len(rows),
        "quant": "int8",
        "scale": 127,
        "built_at": built.isoformat(),
        "version": built.strftime("%Y.%m.%d-%H%M"),
        "downloads_known": known,
        "downloads_unknown": len(rows) - known,
    }
    (outdir / "meta.json").write_text(json.dumps(meta, indent=2))

    kb = (outdir / "vectors.bin").stat().st_size / 1024
    print(f"[embed] wrote {len(rows)} vectors ({kb:.0f} KB), presets.json, meta.json to {outdir}")


if __name__ == "__main__":
    main()
