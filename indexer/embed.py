"""Embed enriched tones and emit the static web artifacts.

Model parity is critical: this uses all-MiniLM-L6-v2 (384-d) via fastembed/ONNX,
the same weights transformers.js loads in the browser (Xenova/all-MiniLM-L6-v2).
Embeddings are L2-normalized then int8-quantized so the browser does cosine as a
plain dot product over `vectors.bin`.
"""
from __future__ import annotations

import argparse
import json
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
                  "mentioned_bands", "mentioned_songs", "gear", "features"]


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
