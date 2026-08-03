"""Backfill: pull every Helix-family tone for bands we already know about.

The browse listing (scrape.py) hard-caps at 50 pages/sort, so tones outside those
windows are never crawled — e.g. an artist's older, low-download covers. CustomTone's
SEARCH endpoint has no such cap and *is* device-scoped, so `/customtone/search/helix/`
returns only Helix-family tones (Helix, LT, Rack, Native — never POD/HD).

This reads the distinct band names already in our data (raw `band` + LLM-normalized
`band_norm`/`bands`), searches each one, and appends any new tones to raw.jsonl. It
DEEPENS bands we know; it does not discover unknown ones (run scrape.py for breadth).

Resumable: searched bands are checkpointed, so a kill never repeats work.
Respects robots Crawl-delay: 10.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import scrape  # reuse parse_tone / flush / HEADERS — search results share the .tone markup

SKIP_TERMS = {"any", "none", "n/a", "na", "various", "unknown", "various artists", "original"}

# Curated ~300 popular guitar-forward artists, weighted to the genres that dominate
# CustomTone (classic/hard rock, metal, prog/djent, punk/alt, blues, shred). This seeds
# the backfill with well-known bands that may have ZERO tones in our data yet — the gap
# that band-deepening alone can't reach (e.g. The Offspring). Deduped case-insensitively
# against the bands we already know, so overlap costs nothing.
SEED_BANDS = [
    # classic / hard rock
    "Led Zeppelin", "Pink Floyd", "The Beatles", "The Rolling Stones", "The Who",
    "Deep Purple", "Queen", "AC/DC", "Aerosmith", "Lynyrd Skynyrd", "Eagles",
    "Fleetwood Mac", "Cream", "Jimi Hendrix", "The Jimi Hendrix Experience", "The Doors",
    "Santana", "ZZ Top", "Boston", "Kansas", "Rush", "Yes", "Genesis", "Dire Straits",
    "Van Halen", "Guns N' Roses", "Bon Jovi", "Def Leppard", "Scorpions", "Whitesnake",
    "Journey", "Foreigner", "Thin Lizzy", "Bad Company", "Free", "Grand Funk Railroad",
    "The Allman Brothers Band", "Creedence Clearwater Revival", "Steppenwolf", "Heart",
    "Toto", "Styx", "REO Speedwagon", "Chicago", "The Eagles", "Blue Oyster Cult",
    "Ted Nugent", "Motley Crue", "Poison", "Ratt", "Skid Row", "Dokken", "Cinderella",
    "Twisted Sister", "Quiet Riot", "Europe", "Kiss", "Alice Cooper", "Rainbow",
    # blues / blues-rock
    "B.B. King", "Stevie Ray Vaughan", "Eric Clapton", "Gary Moore", "Joe Bonamassa",
    "John Mayer", "Buddy Guy", "Albert King", "Freddie King", "Muddy Waters",
    "The Jeff Beck Group", "Jeff Beck", "Rory Gallagher", "Robben Ford", "Peter Green",
    "Gary Clark Jr.", "Kenny Wayne Shepherd", "Walter Trout", "Derek Trucks",
    # metal
    "Metallica", "Iron Maiden", "Black Sabbath", "Judas Priest", "Megadeth", "Slayer",
    "Pantera", "Motorhead", "Anthrax", "Dio", "Ozzy Osbourne", "Lamb of God",
    "Machine Head", "Sepultura", "Testament", "Death", "Meshuggah", "Gojira", "Trivium",
    "Killswitch Engage", "Opeth", "Mastodon", "Slipknot", "System of a Down", "Korn",
    "Disturbed", "Godsmack", "Five Finger Death Punch", "Avenged Sevenfold", "In Flames",
    "Children of Bodom", "Arch Enemy", "Amon Amarth", "Behemoth", "Cannibal Corpse",
    "Dimmu Borgir", "Emperor", "Kreator", "Exodus", "Overkill", "Helloween",
    "Blind Guardian", "Symphony X", "Nevermore", "Fear Factory", "Sepultura",
    "Devildriver", "Trivium", "Whitechapel", "Suicide Silence", "The Black Dahlia Murder",
    "Bullet for My Valentine", "Ghost", "Volbeat", "Mastodon", "Baroness", "High on Fire",
    # prog / djent / instrumental
    "Dream Theater", "Periphery", "TesseracT", "Animals as Leaders", "Polyphia", "Plini",
    "Between the Buried and Me", "Tool", "Porcupine Tree", "Steven Wilson", "Haken",
    "Devin Townsend", "Devin Townsend Project", "Strapping Young Lad", "Leprous",
    "Intervals", "Chon", "Scale the Summit", "Protest the Hero", "Karnivool", "Caligula's Horse",
    # shred / guitar virtuosos
    "Joe Satriani", "Steve Vai", "Eric Johnson", "John Petrucci", "Yngwie Malmsteen",
    "Guthrie Govan", "Andy Timmons", "Paul Gilbert", "Marty Friedman", "Nuno Bettencourt",
    "Extreme", "Racer X", "Mr. Big", "Vinnie Moore", "Tosin Abasi", "Jason Becker",
    "Buckethead", "John 5", "Zakk Wylde", "Black Label Society",
    # alt / grunge / 90s
    "Nirvana", "Pearl Jam", "Soundgarden", "Alice in Chains", "Stone Temple Pilots",
    "Foo Fighters", "Radiohead", "The Smashing Pumpkins", "Weezer", "Red Hot Chili Peppers",
    "Rage Against the Machine", "Audioslave", "Nine Inch Nails", "Faith No More",
    "Jane's Addiction", "Primus", "Tool", "Deftones", "Incubus", "Chevelle", "Staind",
    "Nickelback", "Creed", "Alter Bridge", "Shinedown", "Seether", "Breaking Benjamin",
    "Three Days Grace", "Puddle of Mudd", "Live", "Bush", "Silverchair", "Collective Soul",
    "Filter", "Helmet", "Quicksand", "Mudhoney", "Screaming Trees",
    # punk / pop-punk / emo
    "The Offspring", "Green Day", "Blink-182", "Sum 41", "NOFX", "Bad Religion", "Rancid",
    "Ramones", "The Clash", "Sex Pistols", "Misfits", "Pennywise", "Social Distortion",
    "Rise Against", "Anti-Flag", "Descendents", "Dead Kennedys", "The Damned",
    "My Chemical Romance", "Paramore", "Fall Out Boy", "Panic! at the Disco",
    "A Day to Remember", "Jimmy Eat World", "Taking Back Sunday", "Brand New",
    "The Used", "Thrice", "Alexisonfire", "Underoath", "Silverstein", "New Found Glory",
    # metalcore / modern heavy
    "Bring Me the Horizon", "Architects", "Parkway Drive", "All That Remains",
    "As I Lay Dying", "August Burns Red", "The Devil Wears Prada", "Miss May I",
    "Wage War", "Beartooth", "Knocked Loose", "While She Sleeps", "Currents",
    "Northlane", "Erra", "Spiritbox", "Lorna Shore", "Bad Omens", "Sleep Token",
    # indie / alt rock / modern
    "Arctic Monkeys", "The Strokes", "Muse", "Coldplay", "U2", "The Killers", "Interpol",
    "Kings of Leon", "Franz Ferdinand", "The White Stripes", "The Black Keys", "Queens of the Stone Age",
    "Wolfmother", "Rival Sons", "Greta Van Fleet", "The Raconteurs", "Cage the Elephant",
    "Royal Blood", "Highly Suspect", "Gary Numan", "Placebo", "The Cure", "Joy Division",
    "The Smiths", "R.E.M.", "Pixies", "Sonic Youth", "Dinosaur Jr.",
    # post-rock / ambient / instrumental
    "Explosions in the Sky", "God Is an Astronaut", "Russian Circles", "This Will Destroy You",
    "If These Trees Could Talk", "Pelican", "Mogwai", "Sigur Ros", "Cloudkicker",
    # country / roots with strong guitar
    "Brad Paisley", "Keith Urban", "Brothers Osborne", "Chris Stapleton", "Vince Gill",
    "Dwight Yoakam", "Marcus King", "Lynyrd Skynyrd", "The Marshall Tucker Band",
    # funk / fusion / misc guitar
    "Vulfpeck", "Cory Wong", "John Frusciante", "Prince", "Stevie Wonder",
    "Snarky Puppy", "Tower of Power", "Earth Wind & Fire",
]


def band_terms(raw_path: Path, enriched_path: Path) -> dict[str, str]:
    """Distinct band search terms, keyed lowercase -> first-seen display form.

    Pulls raw `band` from every source (so bands newly discovered by a fresh scrape
    are included) plus the LLM-normalized `band_norm`/`bands` when enrichment exists.
    """
    terms: dict[str, str] = {}

    def add(v):
        if not isinstance(v, str):
            return
        c = v.strip()
        key = c.lower()
        if len(key) >= 2 and not key.isdigit() and key not in SKIP_TERMS:
            terms.setdefault(key, c)

    for path, keys, listkeys in (
        (raw_path, ("band",), ()),
        (enriched_path, ("band", "band_norm", "band_inferred"), ("bands", "mentioned_bands")),
    ):
        if not path.exists():
            continue
        for line in path.read_text().split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            for k in keys:
                add(r.get(k))
            for k in listkeys:
                for x in (r.get(k) or []):
                    add(x)

    for b in SEED_BANDS:  # curated popular bands, incl. ones with zero tones in our data
        add(b)
    return terms


def fetch_search_page(session: requests.Session, term: str, page: int) -> list[dict]:
    if page == 1:
        url = f"https://line6.com/customtone/search/helix/?search_term={quote_plus(term)}"
    else:
        url = f"https://line6.com/customtone/search/helix/{page}/?search_term={quote_plus(term)}"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    out = []
    for t in soup.find_all("div", class_="tone"):
        try:
            r = scrape.parse_tone(t)
        except Exception as e:  # one malformed tone must never abort the crawl
            print(f"[backfill] skipped a tone for '{term}' p{page}: {e}", file=sys.stderr)
            continue
        if r:
            out.append(r)
    return out


CANARY = "metallica"  # the most-covered band on CustomTone; a real 0 here means throttled


def fetch_search_page_safe(session: requests.Session, term: str) -> bool:
    """True if the endpoint is actually serving results right now."""
    try:
        return bool(fetch_search_page(session, term, 1))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="cache/raw.jsonl")
    ap.add_argument("--enriched", default="cache/enriched.jsonl")
    ap.add_argument("--state", default="cache/backfill_state.json")
    ap.add_argument("--delay", type=float, default=10.0, help="seconds between requests (robots Crawl-delay: 10)")
    ap.add_argument("--max-pages", type=int, default=10, help="safety cap per band (10 tones/page)")
    ap.add_argument("--stop-empty", type=int, default=2,
                    help="stop a band after N consecutive pages with zero new tones (the loose-match tail)")
    ap.add_argument("--limit", type=int, default=0, help="0 = all bands; else only the first N pending")
    ap.add_argument("--zero-streak", type=int, default=12,
                    help="consecutive empty bands before suspecting a throttle")
    ap.add_argument("--throttle-wait", type=float, default=900.0,
                    help="seconds to wait between canary probes when throttled")
    ap.add_argument("--throttle-tries", type=int, default=8,
                    help="canary probes before giving up and stopping cleanly")
    ap.add_argument("--restart", action="store_true", help="ignore saved progress and re-search every band")
    args = ap.parse_args()

    out = Path(args.raw)
    known: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text().split("\n"):
            if line.strip():
                r = json.loads(line)
                known[r["id"]] = r
    print(f"[backfill] {len(known)} tones already known", file=sys.stderr)

    terms = band_terms(out, Path(args.enriched))
    state_path = Path(args.state)
    done = set() if args.restart else set(json.loads(state_path.read_text()).get("done", [])
                                          if state_path.exists() else [])
    todo = [(k, v) for k, v in sorted(terms.items()) if k not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"[backfill] {len(terms)} distinct bands, {len(done)} already searched, "
          f"{len(todo)} to do this run", file=sys.stderr)

    session = requests.Session()
    session.headers.update(scrape.HEADERS)

    added = 0
    zero_streak = 0        # consecutive bands that yielded nothing at all
    recent_zero: list = []  # their keys, so a throttle can un-mark them
    for i, (key, disp) in enumerate(todo, 1):
        empty_streak = 0
        got_any = False
        for page in range(1, args.max_pages + 1):
            try:
                tones = fetch_search_page(session, disp, page)
            except requests.HTTPError as e:
                print(f"[backfill] '{disp}' p{page} HTTP {e.response.status_code}, skipping band",
                      file=sys.stderr)
                break
            except Exception as e:
                print(f"[backfill] '{disp}' p{page} error {e}, skipping band", file=sys.stderr)
                break
            if not tones:
                break
            got_any = True
            new_here = 0
            for t in tones:
                if t["id"] not in known:
                    known[t["id"]] = t
                    added += 1
                    new_here += 1
            time.sleep(args.delay)
            if len(tones) < 10:  # short page = last page
                break
            # A band's own presets cluster in the first (relevance-ranked) pages; once pages
            # stop yielding anything new we're into the loose-match tail — bail early.
            empty_streak = empty_streak + 1 if new_here == 0 else 0
            if empty_streak >= args.stop_empty:
                break
        done.add(key)

        # line6.com throttles sustained crawling by returning HTTP 200 with an
        # EMPTY result set — never a 429. A band that yields nothing is then
        # indistinguishable from a band that genuinely has nothing, and since we
        # just marked it done it would never be retried. So: watch for a run of
        # empty bands, and confirm against a query that is never legitimately
        # empty before believing them.
        if got_any:
            zero_streak = 0
            recent_zero.clear()
        else:
            zero_streak += 1
            recent_zero.append(key)

        if zero_streak >= args.zero_streak:
            for attempt in range(1, args.throttle_tries + 1):
                if fetch_search_page_safe(session, CANARY):
                    print(f"[backfill] canary recovered — resuming", file=sys.stderr)
                    break
                # Un-mark the suspect bands so a later pass re-searches them,
                # and persist that immediately: a kill here must not leave them
                # recorded as searched.
                for k in recent_zero:
                    done.discard(k)
                scrape.flush(known, out)
                state_path.write_text(json.dumps({"done": sorted(done)}))
                print(f"[backfill] THROTTLED ({zero_streak} empty bands, canary "
                      f"'{CANARY}' also empty) — un-marked {len(recent_zero)}, "
                      f"waiting {args.throttle_wait}s [{attempt}/{args.throttle_tries}]",
                      file=sys.stderr)
                time.sleep(args.throttle_wait)
            else:
                print("[backfill] still throttled after all retries — stopping cleanly. "
                      "Re-run later; state has the un-marked bands pending.", file=sys.stderr)
                break
            zero_streak = 0
            recent_zero.clear()

        if i % 10 == 0:
            scrape.flush(known, out)
            state_path.write_text(json.dumps({"done": sorted(done)}))
            print(f"[backfill] {i}/{len(todo)} bands searched; +{added} new tones "
                  f"(total {len(known)})", file=sys.stderr)

    scrape.flush(known, out)
    state_path.write_text(json.dumps({"done": sorted(done)}))
    print(f"[backfill] done: searched {len(todo)} bands this run, added {added} new tones, "
          f"total {len(known)}", file=sys.stderr)


if __name__ == "__main__":
    main()
