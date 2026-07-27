"""Scrape Line 6 CustomTone Helix listings into cache/raw.jsonl.

The browse listing is fully server-rendered. Every field we need (name, author,
band/song/guitarist/amp, style, description, date, downloads, rating, device)
lives in each `.tone` block, so no per-tone detail fetches are required.

robots.txt allows /customtone/ but sets Crawl-delay: 10 — respected by default.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://line6.com/customtone/browse/helix/{page}/?sort={sort}&sort_dir={dir}&search_term="
TONE_ID_RE = re.compile(r"/customtone/tone/(\d+)/")
DOWNLOADS_RE = re.compile(r"(\d[\d,]*)\s+downloads?", re.I)
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})")
STAR_RE = re.compile(r"star_(\d)")
LABELS = {"BAND": "band", "SONG": "song", "GUITARIST": "guitarist",
          "AMP": "amp", "TONE NAME": "name"}

HEADERS = {"User-Agent": "helix-tone-search/0.1 (personal preset indexer; respects crawl-delay)"}
STATE_PATH = Path("cache/scrape_state.json")
FLUSH_EVERY = 20  # checkpoint to disk every N pages so a kill never loses progress


def flush(known: dict, out: Path):
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in known.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(out)  # atomic: a kill mid-write can't corrupt the file


def parse_date(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    mm, dd, yy = m.groups()
    year = 2000 + int(yy) if int(yy) < 70 else 1900 + int(yy)
    return f"{year:04d}-{int(mm):02d}-{int(dd):02d}"


def parse_tone(tone) -> dict | None:
    link = tone.find("a", href=TONE_ID_RE)
    if not link:
        return None
    tone_id = TONE_ID_RE.search(link["href"]).group(1)

    rec = {"id": tone_id, "name": None, "author": None, "band": None,
           "song": None, "guitarist": None, "amp": None, "style": None,
           "description": None, "date": None, "downloads": 0, "rating": 0,
           "device": None, "url": f"https://line6.com/customtone/tone/{tone_id}/"}

    author = tone.find("a", href=re.compile(r"/customtone/profile/"))
    if author:
        rec["author"] = author.get_text(strip=True)

    img = tone.find("img", class_="product_icon")
    if img and img.get("alt"):
        rec["device"] = img["alt"].strip()

    # Labeled + unlabeled fields live in the first .details block's <li>s.
    details = tone.find("div", class_="details")
    style_candidates = []
    if details:
        for li in details.find_all("li"):
            txt = li.get_text(" ", strip=True)
            if not txt:
                continue
            m = re.match(r"^([A-Z][A-Z ]+):\s*(.*)$", txt)
            if m and m.group(1) in LABELS:
                rec[LABELS[m.group(1)]] = m.group(2).strip() or None
            elif ":" not in txt:
                style_candidates.append(txt)
    if style_candidates:
        rec["style"] = style_candidates[0]

    comment = tone.find("div", class_="comment")
    if comment:
        rec["description"] = comment.get_text(" ", strip=True) or None

    date_el = tone.find("div", class_="date")
    rec["date"] = parse_date(date_el.get_text(" ", strip=True) if date_el else tone.get_text(" ", strip=True))

    m = DOWNLOADS_RE.search(tone.get_text(" ", strip=True))
    if m:
        digits = m.group(1).replace(",", "")
        if digits.isdigit():
            rec["downloads"] = int(digits)

    rating_ul = tone.find("ul", class_="rating")
    if rating_ul:
        classes = " ".join(rating_ul.get("class", []))
        sm = STAR_RE.search(classes)
        if sm:
            rec["rating"] = int(sm.group(1))

    return rec


def fetch_page(session: requests.Session, page: int, sort: str, direction: str) -> list[dict]:
    url = BASE.format(page=page, sort=sort, dir=direction)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    out = []
    for t in soup.find_all("div", class_="tone"):
        try:
            r = parse_tone(t)
        except Exception as e:  # one malformed tone must never abort the crawl
            print(f"[scrape] skipped a tone on {url}: {e}", file=sys.stderr)
            continue
        if r:
            out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="cache/raw.jsonl")
    ap.add_argument("--sort", default="posted", help="posted|rating|thecount|name|band|song")
    ap.add_argument("--dir", default="desc")
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=1000, help="listing caps at 1000 pages / ~10k tones")
    ap.add_argument("--delay", type=float, default=10.0, help="seconds between requests (robots Crawl-delay: 10)")
    ap.add_argument("--stop-on-seen", action="store_true",
                    help="incremental: stop once a full page of already-known IDs is hit")
    ap.add_argument("--restart", action="store_true",
                    help="ignore saved progress for this sort and start from page 1")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Resume support: remember the last completed page per (sort, dir).
    state_key = f"{args.sort}_{args.dir}"
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    start_page = args.start_page
    if not args.restart and args.start_page == 1 and state.get(state_key):
        start_page = state[state_key] + 1
        print(f"[scrape] resuming {state_key} from page {start_page}", file=sys.stderr)

    known: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text().split("\n"):
            if line.strip():
                r = json.loads(line)
                known[r["id"]] = r
    print(f"[scrape] {len(known)} tones already in {out}", file=sys.stderr)

    session = requests.Session()
    session.headers.update(HEADERS)

    added = 0
    last_page = start_page - 1
    for page in range(start_page, start_page + args.max_pages):
        try:
            tones = fetch_page(session, page, args.sort, args.dir)
        except requests.HTTPError as e:
            print(f"[scrape] page {page} HTTP {e.response.status_code}, stopping", file=sys.stderr)
            break
        if not tones:
            print(f"[scrape] page {page} empty, stopping", file=sys.stderr)
            break

        new_ids = [t["id"] for t in tones if t["id"] not in known]
        for t in tones:
            known[t["id"]] = t
        added += len(new_ids)
        last_page = page
        print(f"[scrape] page {page}: {len(tones)} tones, {len(new_ids)} new "
              f"(total {len(known)})", file=sys.stderr)

        if page % FLUSH_EVERY == 0:
            flush(known, out)
            state[state_key] = last_page
            STATE_PATH.write_text(json.dumps(state))

        if args.stop_on_seen and not new_ids:
            print("[scrape] full page already known, stopping (incremental)", file=sys.stderr)
            break
        if page < start_page + args.max_pages - 1:
            time.sleep(args.delay)

    flush(known, out)
    state[state_key] = last_page
    STATE_PATH.write_text(json.dumps(state))
    print(f"[scrape] wrote {len(known)} tones ({added} new this run, through page {last_page}) to {out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
