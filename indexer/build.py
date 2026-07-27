"""Run the full pipeline: scrape -> enrich -> embed -> web/data artifacts.

Examples:
  python build.py --max-pages 5              # small test crawl
  python build.py --full                     # full ~10k crawl (slow: ~3h at 10s delay)
  python build.py --skip-scrape              # re-enrich + re-embed existing cache/raw.jsonl
  python build.py --skip-scrape --skip-enrich  # just re-embed
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable


def run(mod, *args):
    print(f"\n=== {mod} {' '.join(args)} ===", flush=True)
    subprocess.run([PY, str(HERE / mod), *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="crawl each sort to its depth cap (~50 pages; browse serves only ~500 rows/query)")
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--delay", type=float, default=10.0)
    ap.add_argument("--sorts", default="posted,thecount,rating,name,band,song,guitarist,amp,style",
                    help="comma-separated sort fields to union; each exposes a different ~500-row window")
    ap.add_argument("--tags-only", action="store_true",
                    help="enrichment generates only genre/tone tags; keep uploader band/song as-is")
    ap.add_argument("--skip-scrape", action="store_true")
    ap.add_argument("--skip-enrich", action="store_true")
    args = ap.parse_args()

    if not args.skip_scrape:
        pages = "60" if args.full else str(args.max_pages)
        for s in [s.strip() for s in args.sorts.split(",") if s.strip()]:
            run("scrape.py", "--max-pages", pages, "--delay", str(args.delay), "--sort", s)
    if not args.skip_enrich:
        enrich_args = ["--tags-only"] if args.tags_only else []
        run("enrich.py", *enrich_args)
        run("embed.py", "--in", "cache/enriched.jsonl")
    else:
        run("embed.py", "--in", "cache/raw.jsonl")
    print("\n[build] done -> web/data/{presets.json, vectors.bin, meta.json}")


if __name__ == "__main__":
    main()
