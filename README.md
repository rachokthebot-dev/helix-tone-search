# Helix Tone Search

Semantic search over the [Line 6 CustomTone](https://line6.com/customtone/browse/helix/)
Helix preset marketplace, whose own search is weak. Find presets by **band or song**
first, then narrow by gear, genre, or feel — e.g. `nirvana`, `metallica enter sandman`,
`gilmour lead`, `ambient clean delay`, `djent high-gain`.

**Fully serverless.** A local Python pipeline scrapes, enriches, and embeds the catalog
into three static files. The browser runs semantic search entirely client-side — there
is no backend and no database server. Results deep-link back to CustomTone; the presets
themselves stay on Line 6's cloud, where you download while logged in.

Covers **~2,900 Helix-family tones** (Helix, Helix LT, Rack, Native).

## How it works

```
indexer/ (Python, run locally)                 web/ (static site, GitHub Pages)
  scrape.py        listing -> cache/raw.jsonl    index.html · app.js · style.css
  enrich.py        LLM: genre/tone tags          data/presets.json   metadata
  band_enrich.py   LLM: band/song normalization  data/vectors.bin    int8 embeddings
  embed.py         all-MiniLM-L6-v2 -> vectors    data/meta.json      model + dims
```

In the browser: transformers.js (`Xenova/all-MiniLM-L6-v2`, the same model the indexer
uses) embeds the query → cosine similarity over `vectors.bin` → fused via Reciprocal
Rank Fusion with a MiniSearch keyword index. **Exact band/song matches always rank
first** (grouped by downloads), ahead of semantic neighbors. Filters for device, genre,
and min-downloads persist in `localStorage` across sessions.

## Enrichment

The scraped fields are messy — uploaders type `Rock (Classic, Hard, Progressive)` for
style, spell bands five different ways (`MUSE`, `Muse`), and cram several bands into one
field (`AC/DC, GNR`). Two local-LLM passes clean this up and add the signals search needs.
Both are **concurrent, cached per tone (so re-runs only touch new tones), and resumable.**

**Pass 1 — tags (`enrich.py --tags-only`)** adds, per tone:

- `genre_tags` — normalized genres inferred from the style/name/description
  (`Rock (Classic, Hard, Progressive)` → `["rock","hard rock"]`; Nirvana → `["rock","grunge"]`).
- `tone_tags` — tonal descriptors that exist nowhere in the raw data
  (`["high-gain","distortion"]`, `["clean","delay"]`) — these power the vibe/semantic queries.

  Deliberately **tags-only**: it never touches `band`/`song`, so uploader attribution is
  never "corrected" into something wrong.

**Pass 2 — band normalization (`band_enrich.py`)** makes band/song lookup reliable:

- `band_norm` — the band in canonical form (fix casing/spelling, expand abbreviations:
  `GNR → Guns N' Roses`, `RHCP → Red Hot Chili Peppers`).
- `bands[]` — multi-band fields split into a list (`AC/DC, GNR` → `["AC/DC","Guns N' Roses"]`).
- `aliases[]` — extra search terms: band nicknames and song shorthand (`CAYA → Come As You Are`).
- `band_inferred` / `song_inferred` — **only** when the field is blank *and* the name/description
  clearly implies one; conservative (most blank-band tones are gear-named, so this fills ~3–5%),
  stored separately so the original field is never overwritten and the UI can flag it as a guess.

All of these feed the browser search index (so `GNR`, `Guns N Roses`, and multi-band presets
all match), the embedding text, the color-coded genre families, and the genre facet.

## What CustomTone's browse actually exposes

Reverse-engineered constraints that shape the crawler:

- **~500 rows per query.** The listing hard-caps at 50 pages; the "10000 tones found"
  counter and 1000-page links are UI fiction, and `sort_dir=asc` is ignored. Coverage
  comes from **unioning all nine sort fields** (`posted, thecount, rating, name, band,
  song, guitarist, amp, style`) — each surfaces a different ~500-tone window. The union
  lands at ~2,900 unique tones.
- **robots.txt `Crawl-delay: 10`** — respected by default.
- **Ratings are JS-rendered** and absent from the server HTML, so ranking uses
  **downloads** as the popularity signal.

## Build & refresh the index

`update.sh` runs the whole pipeline on demand — scrape → tag enrichment → band
normalization → embed → commit → push (the push redeploys GitHub Pages):

```bash
./update.sh          # incremental: only new tones are crawled, enriched, embedded (minutes)
./update.sh --full   # rebuild from scratch: re-crawl every sort field (~75 min) + enrich + embed
```

The first run bootstraps the Python venv and installs deps. Enrichment needs a local
OpenAI-compatible LLM at `http://localhost:11500` (adjust `ENDPOINT`/`MODEL` in
`enrich.py` / `band_enrich.py`). Schedule it with cron/launchd for periodic refreshes.

Under the hood each stage is a standalone script — `scrape.py`, `enrich.py`,
`band_enrich.py`, `embed.py` — that you can run on its own. All are **cached per tone,
concurrent, and resumable**, so re-runs only do work for tones that are new.

## Run locally

```bash
cd web && python3 -m http.server 8000     # open http://localhost:8000
```

## Deploy

`web/` is a static site. Pushing to `main` triggers `.github/workflows/pages.yml`, which
publishes `web/` to GitHub Pages. The data files are small (~1 MB vectors + ~1.5 MB JSON),
so no CDN is required.

## Downloading presets

Downloads require a Line 6 login. **Log in at line6.com first**; each result's
"Open on CustomTone" button deep-links to the preset page, where the download works in
your session. The app never handles your credentials.
