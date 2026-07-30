# Helix Tone Search

Semantic search over the [Line 6 CustomTone](https://line6.com/customtone/browse/helix/)
Helix preset marketplace, whose own search is weak. Find presets by **band or song**
first, then narrow by gear, genre, or feel — e.g. `nirvana`, `metallica enter sandman`,
`gilmour lead`, `ambient clean delay`, `djent high-gain`.

**Fully serverless.** A local Python pipeline scrapes, enriches, and embeds the catalog
into three static files. The browser runs semantic search entirely client-side — there
is no backend and no database server. Results deep-link back to CustomTone; the presets
themselves stay on Line 6's cloud, where you download while logged in.

Covers **~8,900 Helix-family tones** (Helix, Helix LT, Rack, Native).

## How it works

```
indexer/ (Python, run locally)                    web/ (static site, GitHub Pages)
  scrape.py          browse listing -> raw.jsonl    index.html · app.js · style.css
  backfill_bands.py  per-band search -> raw.jsonl   data/presets.json   metadata
  enrich_all.py      LLM: tags + band, one pass     data/vectors.bin    int8 embeddings
  embed.py           all-MiniLM-L6-v2 -> vectors    data/meta.json      model + dims
```

(`enrich.py` + `band_enrich.py` are the older two-pass enrichers `enrich_all.py` supersedes.)

In the browser: transformers.js (`Xenova/all-MiniLM-L6-v2`, the same model the indexer
uses) embeds the query → cosine similarity over `vectors.bin` → fused via Reciprocal
Rank Fusion with a MiniSearch keyword index. **Exact band/song matches always rank
first** (grouped by downloads), ahead of semantic neighbors. Filters for device, genre,
and min-downloads persist in `localStorage` across sessions.

## Enrichment

The scraped fields are messy — uploaders type `Rock (Classic, Hard, Progressive)` for
style, spell bands five different ways (`MUSE`, `Muse`), and cram several bands into one
field (`AC/DC, GNR`). A single local-LLM pass (`enrich_all.py`) cleans this up and adds the
signals search needs — **one call per tone, concurrent, cached (so re-runs only touch new
tones), and resumable.** A rule-based fast-path skips the LLM entirely for no-description
tones whose `style` maps cleanly, and only calls the model when there's real work to do.

**Classification fields** — added per tone:

- `genre_tags` — normalized genres inferred from the style/name/description
  (`Rock (Classic, Hard, Progressive)` → `["rock","hard rock"]`; Nirvana → `["rock","grunge"]`).
- `tone_tags` — tonal descriptors that exist nowhere in the raw data
  (`["high-gain","distortion"]`, `["clean","delay"]`) — these power the vibe/semantic queries.

  Classification **never overwrites `band`/`song`** — attribution lives in its own fields
  (below), so uploader credits are never "corrected" into something wrong.

**Attribution fields** — make band/song lookup reliable:

- `band_norm` — the band in canonical form (fix casing/spelling, expand abbreviations:
  `GNR → Guns N' Roses`, `RHCP → Red Hot Chili Peppers`).
- `bands[]` — multi-band fields split into a list (`AC/DC, GNR` → `["AC/DC","Guns N' Roses"]`).
- `aliases[]` — extra search terms: band nicknames and song shorthand (`CAYA → Come As You Are`).
- `band_inferred` / `song_inferred` — **only** when the field is blank *and* the name/description
  clearly implies one; conservative (most blank-band tones are gear-named, so this fills ~3–5%),
  stored separately so the original field is never overwritten and the UI can flag it as a guess.

All of these feed the browser search index (so `GNR`, `Guns N Roses`, and multi-band presets
all match), the embedding text, the color-coded genre families, and the genre facet.

## Search & UI

- **Band / song / artist first.** Exact band, song, or guitarist matches float to the top
  (grouped by downloads), ahead of everything else — the primary use case. Normalized band
  fields mean `GNR`, `Guns N' Roses`, multi-band presets, and description-mentioned bands
  all resolve.
- **Hybrid ranking.** In-browser semantic embeddings (transformers.js) fused with a
  MiniSearch keyword index via Reciprocal Rank Fusion. Literal matches show first; a
  labelled **"Related tones"** divider separates the semantic tail so weaker neighbours
  aren't mistaken for real hits.
- **No-match is explicit.** A query with no literal match reads **"0 matches · N similar"**
  with a banner — never a silent list of neighbours presented as results.
- **Structured filters & chips.** Device / Genre / Feature / min-downloads facets;
  genre-family **colour-coded** card stripes; **gear** shown as click-to-filter chips; a
  *"General-purpose preset"* label for tones with no band/song. Filter/sort selections
  persist in `localStorage`.
- **Search-first blank screen** with a cycling typewriter of example queries. Every result
  deep-links to CustomTone; presets are never hosted here.

## What CustomTone's browse actually exposes

Reverse-engineered constraints that shape the crawler:

- **~500 rows per window.** The browse listing hard-caps at 50 pages; the "10000 tones
  found" counter and 1000-page links are UI fiction. `scrape.py` widens coverage by
  **unioning all nine sort fields** (`posted, thecount, rating, name, band, song,
  guitarist, amp, style`) **× both directions** — `sort_dir=asc` surfaces the *opposite*
  ~500-tone tail from `desc`, so each combination is a distinct window.
- **The browse `search_term` is silently ignored** — but `/customtone/search/helix/?search_term=X`
  *is* a real, device-scoped filter. `backfill_bands.py` uses it to pull every Helix tone
  for each band already in the index, plus a curated list of ~300 popular bands — this is
  what reaches an artist's older, low-download covers the sort windows never page to.
- Browse windows + band backfill together land at **~8,900 unique tones**.
- **robots.txt `Crawl-delay: 10`** — respected by default.
- **Ratings are JS-rendered** and absent from the server HTML, so ranking uses
  **downloads** as the popularity signal.

## Build & refresh the index

`update.sh` runs the whole pipeline on demand — scrape → band backfill → enrich → embed →
commit → push (the push redeploys GitHub Pages):

```bash
./update.sh          # incremental: only new tones are crawled, enriched, embedded (minutes)
./update.sh --full   # rebuild from scratch: re-crawl every sort × direction + per-band
                     # backfill + enrich + embed (several hours at the 10s crawl-delay)
```

The band backfill only runs on `--full` (it searches ~1,500 bands, hours at the crawl-delay);
incremental runs skip it and just pick up newly-posted tones at the top of each sort.

The first run bootstraps the Python venv and installs deps. Enrichment needs a local
OpenAI-compatible LLM at `http://localhost:11500` (adjust `ENDPOINT`/`MODEL` in
`enrich_all.py`). Schedule it with cron/launchd for periodic refreshes.

Under the hood each stage is a standalone script — `scrape.py`, `backfill_bands.py`,
`enrich_all.py`, `embed.py` — that you can run on its own. All are **cached per tone,
concurrent, and resumable**, so re-runs only do work for tones that are new. For a long
`--full` run, wrap it in `caffeinate` so the machine doesn't sleep mid-crawl.

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

## Legal & attribution

A **non-commercial, fan-made** discovery tool — not affiliated with, sponsored by, or
endorsed by Line 6 or Yamaha Guitar Group. Catalog *metadata* is drawn from Line 6
CustomTone with the crawler respecting `robots.txt` (`Crawl-delay: 10`). The preset files
themselves are **never downloaded or redistributed here** — results deep-link to CustomTone,
where the presets remain the copyright of their authors. Review Line 6 / Yamaha's
[Terms of Use](https://yamahaguitargroup.com/termsofuse) before any public or commercial use.
