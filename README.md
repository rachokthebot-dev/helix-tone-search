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
indexer/ (Python, run locally)              web/ (static site, GitHub Pages)
  scrape.py   listing  -> cache/raw.jsonl     index.html · app.js · style.css
  enrich.py   local LLM adds genre/tone tags   data/presets.json   metadata
  embed.py    all-MiniLM-L6-v2 -> vectors       data/vectors.bin    int8 embeddings
                                                data/meta.json      model + dims
```

In the browser: transformers.js (`Xenova/all-MiniLM-L6-v2`, the same model the indexer
uses) embeds the query → cosine similarity over `vectors.bin` → fused via Reciprocal
Rank Fusion with a MiniSearch keyword index. **Exact band/song matches always rank
first** (grouped by downloads), ahead of semantic neighbors. Filters for device, genre,
and min-downloads persist in `localStorage` across sessions.

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

## Build the index

```bash
cd indexer
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build.py --full --tags-only     # full union crawl (~75 min at 10s delay) + enrich + embed
```

- `--full` crawls each sort field to its ~50-page cap and unions the results.
- `--tags-only` enrichment adds `genre_tags`/`tone_tags` and never invents band/song
  (uploaders already fill those; the LLM only guesses when it shouldn't).
- Enrichment calls a local OpenAI-compatible LLM at `http://localhost:11500` — adjust
  `ENDPOINT`/`MODEL` in `enrich.py`, or `--skip-enrich`.
- Re-runs are incremental (per-sort resume state) and enrichment is cached per tone.

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
