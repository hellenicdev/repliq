"""Index the whole archive.org subtitled movie library into MongoDB.

Enumerates every archive.org item that has an SRT/WebVTT subtitle file,
then persists its dialogue segments in PRIORITY ORDER until a storage
budget is reached (Atlas free tier ~512 MB). Nothing is transcribed and
no video is downloaded - clips are cut from archive.org lazily at
generation time.

Priorities (highest first):
  1. items in curated film collections (feature_films, classic_films, ...)
  2. items with an imdb id
  3. everything else (TV netcasts, news, ...)

The crawler is resumable: a checkpoint file records every identifier it
has already persisted, and the videos collection has a unique index on
sourceUrl so re-runs can never duplicate anything.

Usage (from backend/):
    python scripts/index_library.py                 # full priority-fill run
    python scripts/index_library.py --max-items 50  # bounded test run
    python scripts/index_library.py --dry-run 50    # list candidates only
    python scripts/index_library.py --reset-checkpoint
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.utils.text import normalize_text, tokenize  # noqa: E402

BASE = "https://archive.org"
SEARCH_URL = f"{BASE}/advancedsearch.php"
DOWNLOAD_URL = f"{BASE}/download"

# --- crawl parameters -------------------------------------------------------
START_YEAR = 1996
END_YEAR = datetime.now().year + 1
QUERY_ROWS = 1000  # advancedsearch cap per page
QUERY_CAP = 9000  # if numFound approaches 10k, sub-chunk by month/day
BUDGET_SEGMENTS = 1_000_000  # measured: 292 B data + 143 B index per segment; 1M fits the 512 MB tier
POLITE_INTERVAL = 0.35  # seconds between archive.org requests
MAX_RETRIES = 5

FEATURE_COLLECTIONS = {
    "feature_films", "classic_films", "silent_films", "moviesandfilms",
    "feature_films_unsorted", "prelinger", "b-movies", "monsters_archive",
}
TV_NOISE = re.compile(
    r"(_\d{6,}|netcast|_TV_| - TV | News | Documentary|Episode|S\d{2}E\d{2}|"
    r"^RT_|^DW_|^M1_|ESPRESO|recording|stream)", re.I,
)
TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*--?>\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)
TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
CUE_RE = re.compile(r"^[(\[]?(music|applause|laughter|typing|whistling|screaming|man|woman|speech)[)\]]?$", re.I)
MIN_DURATION = 0.4
MAX_DURATION = 20.0

CHECKPOINT = Path(__file__).resolve().parent / ".index_library_checkpoint.json"

# Priority passes: curated film collections -> imdb-tagged -> everything else.
COLL_CLAUSE = " OR ".join(f"collection:{c}" for c in sorted(FEATURE_COLLECTIONS))
PASSES = [
    f"mediatype:movies AND format:SubRip AND ({COLL_CLAUSE})",
    "mediatype:movies AND format:SubRip AND imdb:[* TO *]",
    "mediatype:movies AND format:SubRip",
]


def hms_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def clean_text(raw: str) -> str | None:
    text = TAG_RE.sub(" ", raw).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or CUE_RE.match(text):
        return None
    return text


def parse_srt(content: str) -> list[dict]:
    segments = []
    for block in re.split(r"\n\s*\n", content):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        time_match = None
        text_idx = 1
        for i, ln in enumerate(lines):
            m = TIME_RE.search(ln)
            if m:
                time_match = m
                text_idx = i + 1
                break
        if not time_match:
            continue
        raw = " ".join(lines[text_idx:])
        text = clean_text(raw)
        if not text:
            continue
        start = hms_to_seconds(*time_match.groups()[:4])
        end = hms_to_seconds(*time_match.groups()[4:])
        if end - start < MIN_DURATION or end - start > MAX_DURATION:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segments


def parse_vtt(content: str) -> list[dict]:
    """Convert a WebVTT file to SRT format, then reuse the SRT parser."""
    blocks = []
    cur = []
    for line in content.splitlines():
        line = line.rstrip()
        if not line.strip() and cur:
            blocks.append("\n".join(cur))
            cur = []
        elif line.strip() and not line.strip().startswith(("WEBVTT", "NOTE", "STYLE")):
            cur.append(line.replace(".", ",", 2))
    if cur:
        blocks.append("\n".join(cur))
    return parse_srt("\n\n".join(blocks))


def score_item(doc: dict) -> int:
    s = 0
    colls = {c.lower() for c in (doc.get("collection") or [])}
    if colls & FEATURE_COLLECTIONS:
        s += 100
    if doc.get("imdb"):
        s += 30
    title = f"{doc.get('title', '')} {doc.get('identifier', '')}"
    if TV_NOISE.search(title):
        s -= 50
    return s


def get_json(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"unreachable: {url}")


def search_page(client: httpx.Client, query: str, page: int) -> tuple[list[dict], int]:
    data = get_json(client, SEARCH_URL, {
        "q": query,
        "fl[]": ["identifier", "title", "collection", "imdb", "addeddate"],
        "rows": QUERY_ROWS,
        "page": page,
        "output": "json",
    })
    resp = data["response"]
    return resp.get("docs", []), resp.get("numFound", 0)


def iter_search_docs(client: httpx.Client, base_query: str, since: str | None, stop_after: int | None = None):
    """Yield all docs matching base_query, chunked by year -> month -> day.

    When base_query already returns few results (e.g. curated collections),
    pass unchunked=True through since='*' to skip date chunking.
    """
    yielded = 0
    if since == "*":
        page = 1
        while True:
            docs, num = search_page(client, base_query, page)
            for d in docs:
                if stop_after is not None and yielded >= stop_after:
                    return
                yielded += 1
                yield d
            if page * QUERY_ROWS >= num:
                break
            page += 1
            time.sleep(POLITE_INTERVAL)
        return
    for year in range(START_YEAR, END_YEAR):
        y0 = datetime(year, 1, 1)
        y1 = datetime(year + 1, 1, 1)
        if since and (year, 0, 0) < tuple(int(x) for x in (since[0:4], 0, 0)):
            continue
        for month in range(1, 13):
            m0 = datetime(year, month, 1)
            m1 = (m0 + timedelta(days=32)).replace(day=1)
            if since and (year, month) < (int(since[0:4]), int(since[5:7])):
                continue
            query = (
                f'{base_query} AND '
                f'addeddate:[{m0:%Y-%m-%d} TO {m1 - timedelta(days=1):%Y-%m-%d}]'
            )
            _, num = search_page(client, query, 1)
            if num > QUERY_CAP:
                for day in range(1, 32):
                    d0 = datetime(year, month, day)
                    if d0 >= m1:
                        break
                    d1 = (d0 + timedelta(days=1))
                    if d1 > m1:
                        d1 = m1
                    if since and (year, month, day) < tuple(int(x) for x in since.split("-")):
                        continue
                    sub_query = (
                        f'{base_query} AND '
                        f'addeddate:[{d0:%Y-%m-%d} TO {d1 - timedelta(days=1):%Y-%m-%d}]'
                    )
                    page = 1
                    while True:
                        docs, sub_num = search_page(client, sub_query, page)
                        for d in docs:
                            if stop_after is not None and yielded >= stop_after:
                                return
                            yielded += 1
                            yield d
                        if page * QUERY_ROWS >= sub_num:
                            break
                        page += 1
                        time.sleep(POLITE_INTERVAL)
            else:
                page = 1
                while True:
                    docs, sub_num = search_page(client, query, page)
                    for d in docs:
                        if stop_after is not None and yielded >= stop_after:
                            return
                        yielded += 1
                        yield d
                    if page * QUERY_ROWS >= sub_num:
                        break
                    page += 1
                    time.sleep(POLITE_INTERVAL)
            time.sleep(POLITE_INTERVAL)


def pick_srt(files: list[dict]) -> dict | None:
    cands = [f for f in files if re.search(r"\.(srt|vtt)$", f.get("name", ""), re.I)]
    if not cands:
        return None
    return min(cands, key=lambda f: float(f.get("size") or 1e18))


def pick_video(files: list[dict]) -> dict | None:
    cands = [f for f in files if re.search(r"\.mp4$", f.get("name", ""), re.I)]
    if not cands:
        return None
    return min(cands, key=lambda f: float(f.get("size") or 1e18))


def load_checkpoint() -> set[str]:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text(encoding="utf-8")).get("indexed", []))
    return set()


def save_checkpoint(identifiers: set[str]) -> None:
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"indexed": sorted(identifiers)}), encoding="utf-8")
    tmp.replace(CHECKPOINT)


def process_item(http: httpx.Client, db, doc: dict, done_ids: set[str], total_segments: int) -> int:
    """Index one item's SRT into Mongo. Returns the updated total segment count."""
    ident = doc.get("identifier", "")
    if not ident or ident in done_ids:
        return total_segments
    from pymongo.errors import DuplicateKeyError

    try:
        resp = get_json(http, f"{BASE}/metadata/{ident}")
        meta = resp.get("metadata", {})
        files = resp.get("files", [])
        srt = pick_srt(files)
        if not srt:
            return total_segments
        srt_url = f"{DOWNLOAD_URL}/{ident}/{srt['name']}"
        content = http.get(srt_url, timeout=120).text
        segments = parse_vtt(content) if srt["name"].lower().endswith(".vtt") else parse_srt(content)
        if not segments:
            return total_segments
        video = pick_video(files)
        if not video:
            return total_segments
        video_url = f"{DOWNLOAD_URL}/{ident}/{video['name']}"

        doc_obj = {
            "title": meta.get("title") or doc.get("title") or ident,
            "source": "archive.org",
            "fileUrl": f"media/source/{video['name']}",
            "sourceUrl": video_url,
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
            "imdbId": (doc.get("imdb") or "").split("/title/")[-1].rstrip("/") if doc.get("imdb") else None,
            "collection": list(set(doc.get("collection") or [])),
            "addedAt": datetime.now(timezone.utc).isoformat(),
        }
        video_id = db.videos.insert_one(doc_obj).inserted_id
        db.dialogue.insert_many(
            [
                {
                    "videoId": str(video_id),
                    "character": "Unknown",
                    "text": s["text"],
                    "startTime": s["start"],
                    "endTime": s["end"],
                    "confidence": 0.95,
                    "tokenCount": len(tokenize(s["text"])),
                    "normalizedText": normalize_text(s["text"]),
                }
                for s in segments
            ],
            ordered=False,
        )
        total_segments += len(segments)
        done_ids.add(ident)
        print(f"  [{len(done_ids)}] {ident} | {len(segments):5d} seg | {str(meta.get('title'))[:45]} | total {total_segments}", flush=True)
        time.sleep(POLITE_INTERVAL)
    except DuplicateKeyError:
        done_ids.add(ident)
    except Exception as e:
        print(f"  ! {ident}: {str(e)[:120]}", flush=True)
        time.sleep(1)
    return total_segments


def main() -> None:
    parser = argparse.ArgumentParser(description="Priority-fill archive.org subtitled library indexer")
    parser.add_argument("--max-items", type=int, default=None, help="process at most N items this run")
    parser.add_argument("--budget-segments", type=int, default=BUDGET_SEGMENTS)
    parser.add_argument("--since", default=None, help="YYYY-MM-DD addeddate cursor")
    parser.add_argument("--dry-run", type=int, default=0, help="only list top-N candidates, no writes")
    parser.add_argument("--reset-checkpoint", action="store_true")
    args = parser.parse_args()

    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError

    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    db = mongo[settings.mongodb_database]
    try:
        db.videos.create_index("sourceUrl", unique=True, sparse=True)
    except Exception as e:
        print("index note:", e)

    done_ids = set() if args.reset_checkpoint else load_checkpoint()
    if args.reset_checkpoint:
        save_checkpoint(done_ids)

    http = httpx.Client(timeout=60, follow_redirects=True)
    print(f"Target: {settings.mongodb_uri}")
    print(f"Already indexed: {len(done_ids)} items | budget: {args.budget_segments} segments | "
          f"current library: {db.videos.count_documents({})} videos / {db.dialogue.count_documents({})} segments")

    if args.dry_run:
        docs = list(iter_search_docs(http, PASSES[0], "*", args.dry_run))
        docs.sort(key=score_item, reverse=True)
        for d in docs[: args.dry_run]:
            print(f"  {score_item(d):4d}  {d.get('identifier')} | {str(d.get('title'))[:50]} | imdb={bool(d.get('imdb'))}")
        return

    total_segments = db.dialogue.count_documents({})
    processed = 0
    hits = 0

    print("Enumerating candidates in priority order...", flush=True)
    seen: set[str] = set()
    attempts = 0
    for base in PASSES:
        if args.max_items and hits >= args.max_items:
            break
        for d in iter_search_docs(http, base, "*" if base == PASSES[0] else args.since):
            ident = d.get("identifier", "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            attempts += 1
            if args.max_items and attempts > args.max_items * 25:
                print(f"Attempt cap reached ({attempts} attempts, {hits} hits). Stopping.", flush=True)
                break
            if total_segments >= args.budget_segments:
                print(f"Budget reached ({total_segments} segments). Stopping.", flush=True)
                break
            if args.max_items and hits >= args.max_items:
                break
            before = len(done_ids)
            total_segments = process_item(http, db, d, done_ids, total_segments)
            if len(done_ids) > before:
                hits += 1
            processed += 1
            if done_ids and len(done_ids) % 200 == 0:
                save_checkpoint(done_ids)
        if total_segments >= args.budget_segments:
            break

    save_checkpoint(done_ids)
    print(f"\nDone this run: {processed} items ({hits} new), library now "
          f"{db.videos.count_documents({})} videos / {db.dialogue.count_documents({})} segments")


if __name__ == "__main__":
    main()
