"""Find archive.org uploads that carry subtitles for iconic public-domain films.

Zero-cost discovery: one advancedsearch query per title (format:SubRip), no
metadata fetches, no downloads. Best candidates are ranked by film-collection
membership + IMDb id and can be appended to famous_films.txt for the crawler's
--famous-file pre-pass.

Usage (from backend/):
    python scripts/find_srt.py                    # print candidates only
    python scripts/find_srt.py --write            # append best hits to famous_films.txt
    python scripts/find_srt.py --titles-file list.txt --limit 8
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEARCH_URL = "https://archive.org/advancedsearch.php"
POLITE_INTERVAL = 0.4
FAMOUS_FILE = Path(__file__).resolve().parent / "famous_films.txt"

DEFAULT_TITLES = [
    "Dracula 1931",
    "Frankenstein 1931",
    "King Kong 1933",
    "The Invisible Man 1933",
    "The Mummy 1932",
    "Bride of Frankenstein",
    "The Wolf Man 1941",
    "His Girl Friday",
    "The Thin Man",
    "Arsenic and Old Lace",
    "Night of the Living Dead",
    "The General 1926",
    "Nosferatu 1922",
    "Metropolis 1927",
    "It Happened One Night",
    "Mr. Smith Goes to Washington",
    "The Maltese Falcon",
    "The Third Man",
    "The 39 Steps",
    "The Most Dangerous Game",
    "White Zombie",
    "The Phantom of the Opera 1925",
]

GOOD_COLLECTIONS = {
    "feature_films", "classic_films", "silent_films", "moviesandfilms",
    "feature_films_unsorted", "b-movies", "monsters_archive",
}


def score(doc: dict) -> int:
    s = 0
    colls = {c.lower() for c in (doc.get("collection") or [])}
    if colls & GOOD_COLLECTIONS:
        s += 50
    if doc.get("imdb"):
        s += 30
    return s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="append best hit per title to famous_films.txt")
    parser.add_argument("--limit", type=int, default=6, help="candidates shown per title")
    parser.add_argument("--titles-file", default=None, help="one title per line (default: built-in list)")
    args = parser.parse_args()

    titles = DEFAULT_TITLES
    if args.titles_file:
        titles = [ln.strip() for ln in Path(args.titles_file).read_text(encoding="utf-8").splitlines() if ln.strip()]

    client = httpx.Client(timeout=30)
    found: list[str] = []

    for title in titles:
        print(f"\n=== {title} ===", flush=True)
        resp = client.get(SEARCH_URL, params={
            "q": f'title:"{title}" AND mediatype:movies AND format:SubRip',
            "fl[]": ["identifier", "title", "collection", "imdb"],
            "rows": args.limit,
            "output": "json",
        }, timeout=60)
        if resp.status_code != 200:
            print(f"  query failed (HTTP {resp.status_code})")
            time.sleep(POLITE_INTERVAL)
            continue
        docs = resp.json().get("response", {}).get("docs", [])
        docs.sort(key=score, reverse=True)
        if not docs:
            print("  no SubRip uploads found")
            continue
        for d in docs:
            print(f"  {score(d):3d}  {d.get('identifier')} | {str(d.get('title'))[:48]}")
        if args.write:
            found.append(docs[0]["identifier"])
        time.sleep(POLITE_INTERVAL)

    if args.write and found:
        existing = set()
        if FAMOUS_FILE.exists():
            existing = {ln.strip() for ln in FAMOUS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
        new = [f for f in found if f not in existing]
        with FAMOUS_FILE.open("a", encoding="utf-8") as fh:
            for ident in new:
                fh.write(f"{ident}\n")
        print(f"\nAppended {len(new)} identifiers to {FAMOUS_FILE.name}")


if __name__ == "__main__":
    main()