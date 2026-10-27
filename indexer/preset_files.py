"""Facts read from the downloaded .hlx files, replacing guesses from the description.

The author's text only sometimes says how many snapshots a preset has or whether it
needs third-party IRs; the preset file always knows. Tones whose file isn't in the
local library (fetch_library.py in tone-preview) keep the text-derived values.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

LIBRARY = Path(os.environ.get("HELIX_LIBRARY", Path.home() / "helix-library")) / "presets"


def _audible(snap: dict) -> str:
    # name, LED colour and footswitch state don't change what you hear
    return json.dumps({"c": snap.get("controllers"), "b": snap.get("blocks"),
                       "t": snap.get("@tempo")}, sort_keys=True)


def _pretty_amp(model: str) -> str:
    name = re.sub(r"^HD2_(Pre)?[Aa]mp", "", model)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z])(?=\d)", " ", name)
    return name + " Pre" if "_Preamp" in model and not name.endswith("Pre") else name


def describe(doc: dict) -> dict:
    tone = doc["data"]["tone"]
    snaps = [tone[k] for k in sorted(tone) if k.startswith("snapshot")]
    # Every file carries all 8 slots. A slot is in use only if it's flagged valid
    # (newer firmware) and actually stores block/controller state — an empty slot
    # recalls nothing, and counting it made ~2,000 single-sound presets read as two.
    flagged = any("@valid" in s for s in snaps)
    used = [s for s in snaps if (s.get("@valid") or not flagged)
            and (s.get("blocks") or s.get("controllers"))]
    distinct = len({_audible(s) for s in used})

    amps, user_ir = set(), False
    for dsp in ("dsp0", "dsp1"):
        for key, block in tone.get(dsp, {}).items():
            model = block.get("@model", "") if isinstance(block, dict) else ""
            if model.startswith(("HD2_Amp", "HD2_Preamp")):
                amps.add(_pretty_amp(model))
            # HD2_CabMicIr_* are factory cabs; HD2_ImpulseResponse* load a user IR
            # slot. A bypassed IR block only matters if some snapshot switches it on.
            if model.startswith("HD2_ImpulseResponse") and (
                    block.get("@enabled")
                    or any((s.get("blocks") or {}).get(dsp, {}).get(key) for s in used)):
                user_ir = True
    return {"snapshots": distinct if distinct >= 2 else None,
            "needs_ir": user_ir, "amps": sorted(amps)}


def apply(rows: list[dict]) -> int:
    """Overwrite snapshots/needs_ir and add amps for every row with a local file."""
    n = 0
    for r in rows:
        f = LIBRARY / f"{r['id']}.hlx"
        if not f.exists():
            continue
        try:
            facts = describe(json.loads(f.read_bytes()))
        except (ValueError, KeyError):
            continue
        r.update(facts, from_file=True)
        n += 1
    return n
