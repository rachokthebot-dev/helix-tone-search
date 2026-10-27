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


# Effect blocks present in the chain, named as the Feature facet already names them.
EFFECTS = [
    ("wah", r"_Wah"), ("pitch shifter", r"_Pitch|L6SPB_Poly|Octaver|Harmony|TwelveString"),
    ("looper", r"Looper"), ("compressor", r"Compressor|DM4\w*Comp"), ("noise gate", r"_Gate"),
    ("drive", r"_Dist"), ("tremolo", r"Tremolo"), ("phaser", r"Phaser|MM4\w*Phase"),
    ("flanger", r"Flang"), ("chorus", r"Chorus|Dimension"), ("rotary", r"Rotary"),
    ("vibrato", r"Vibrato|FlexoVibe"), ("delay", r"Delay|DL4|Echo"), ("reverb", r"Reverb|VIC_Dyn"),
    ("eq", r"_EQ|CaliQ"), ("filter", r"_Filter|Sweeper"), ("synth", r"Synth|FM4"),
    ("fx loop", r"FXLoop|_Send|_Return"), ("acoustic sim", r"AcousGtrSim"),
    ("ring modulator", r"RingMod"),
]
# LLM feature tags the file now answers outright, mapped to the name above.
SUPERSEDED = {"snapshots", "ir", "comp", "compression", "gate", "flange", "octave", "octaver",
              "whammy", "echo", "stereo delay", "eqs", "parametric eq", "drives", "overdrive",
              "distortion"} | {name for name, _ in EFFECTS}
# What a snapshot is for, read from its name. Names are capped at ~10 characters,
# so authors abbreviate ("cln", "ld", "prc amb").
ROLES = [
    ("clean", r"cle?a?n\b|^cl\b|clean"), ("crunch", r"crunch|crnch|edge|dirt|drive|drv|\bod\b|grit"),
    ("rhythm", r"rhythm|rhy|rthm|riff"), ("lead", r"lead|\bld\b|solo|boost"),
    ("heavy", r"heavy|metal|hi-?gain|high ?gain|djent|chug"), ("ambient", r"amb|swell|shimmer|\bpad|wash"),
]
SECTIONS = re.compile(r"\b(intro|verse|bridge|outro|pre-?ch)", re.I)
DEFAULT_NAME = re.compile(r"^(snapshot ?\d|empty)?$", re.I)


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

    amps, effects, user_ir = set(), set(), False
    for dsp in ("dsp0", "dsp1"):
        for key, block in tone.get(dsp, {}).items():
            model = block.get("@model", "") if isinstance(block, dict) else ""
            if model.startswith(("HD2_Amp", "HD2_Preamp")):
                amps.add(_pretty_amp(model))
            effects.update(name for name, pat in EFFECTS if re.search(pat, model))
            # HD2_CabMicIr_* are factory cabs; HD2_ImpulseResponse* load a user IR
            # slot. A bypassed IR block only matters if some snapshot switches it on.
            if model.startswith("HD2_ImpulseResponse") and (
                    block.get("@enabled")
                    or any((s.get("blocks") or {}).get(dsp, {}).get(key) for s in used)):
                user_ir = True
    names = [n.strip() for n in (s.get("@name") or "" for s in used) if not DEFAULT_NAME.match(n.strip())]
    roles = {f"{role} snapshot" for role, pat in ROLES for n in names if re.search(pat, n, re.I)}
    if sum(bool(SECTIONS.search(n)) for n in names) >= 2:
        roles.add("song sections")      # intro/verse/bridge: mapped to one song's structure

    return {"snapshots": distinct if distinct >= 2 else None,
            "needs_ir": user_ir, "amps": sorted(amps),
            "effects": sorted(effects), "roles": sorted(roles)}


def apply(rows: list[dict]) -> int:
    """Replace text-derived facts with the file's for every row with a local file.

    The file's band/song fields are not used: they are the same fields the crawl
    already scrapes, and the few the index lacks are junk ("CLEAN", "Multiple").
    """
    n = 0
    for r in rows:
        f = LIBRARY / f"{r['id']}.hlx"
        if not f.exists():
            continue
        try:
            facts = describe(json.loads(f.read_bytes()))
        except (ValueError, KeyError):
            continue
        effects, roles = facts.pop("effects"), facts.pop("roles")
        kept = [x for x in (r.get("features") or []) if x.strip().lower() not in SUPERSEDED
                and not x.endswith(" snapshot") and x != "song sections"]  # idempotent
        r["features"] = (kept + effects + roles + ["snapshots"] * bool(facts["snapshots"])
                         + ["IR"] * facts["needs_ir"])
        r.update(facts, from_file=True)
        n += 1
    return n
