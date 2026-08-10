"""Index a public-domain film from the Internet Archive into MongoDB.

Downloads nothing — only the item's metadata API is queried. The dialogue
segments are parsed from the item's subtitle file (ASR SRT), giving real
timestamps. The actual video is fetched lazily by the backend the first
time a generation needs a clip from it.

Usage (from backend/):
    python scripts/index_pd_film.py --identifier CarnivalOfSouls1962
    python scripts/index_pd_film.py --identifier <id> --video <file.mp4> --srt <file.srt> --title "Name"
"""

import argparse
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.utils.text import normalize_text, tokenize  # noqa: E402

BASE_URL = "https://archive.org"
TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*--?>\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)
TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
CUE_RE = re.compile(r"^[(\[]?(music|applause|laughter|typing|whistling|screaming|man|woman)[)\]]?$", re.I)
SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z .'\-]+):\s*(.+)$")


def hms_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def clean_text(raw: str) -> str | None:
    text = TAG_RE.sub(" ", raw).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or CUE_RE.match(text):
        return None
    return text


def parse_srt(content: str) -> list[dict]:
    """Parse SRT blocks into {start, end, text} dicts."""
    blocks = re.split(r"\n\s*\n", content)
    segments = []
    for block in blocks:
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
        if end - start < 0.4 or end - start > 20:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segments


def pick_file(files: list[dict], pattern: str, preferred: list[str] | None = None) -> dict | None:
    candidates = [f for f in files if re.search(pattern, f.get("name", ""), re.I)]
    if not candidates:
        return None
    if preferred:
        for pref in preferred:
            for f in candidates:
                if pref.lower() in f["name"].lower():
                    return f
    return min(candidates, key=lambda f: float(f.get("size") or 1e18))


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a public-domain film from archive.org")
    parser.add_argument("--identifier", default="CarnivalOfSouls1962")
    parser.add_argument("--video", help="file name inside the item (default: smallest mp4)")
    parser.add_argument("--srt", help="subtitle file name inside the item (default: first .srt/.vtt)")
    parser.add_argument("--title", help="display title (default: item title)")
    parser.add_argument("--dry-run", action="store_true", help="only print what would be indexed")
    args = parser.parse_args()

    with httpx.Client(timeout=30) as client:
        meta = client.get(f"{BASE_URL}/metadata/{args.identifier}").json()
        if meta.get("error") or not meta.get("files"):
            sys.exit(f"item not found: {args.identifier}")
        files = meta["files"]
        title = args.title or meta.get("metadata", {}).get("title", args.identifier)

        srt_file = pick_file(files, r"\.(srt|vtt)$")
        if args.srt:
            srt_file = next((f for f in files if f["name"] == args.srt), None)
        if not srt_file:
            sys.exit(f"no subtitle file in item {args.identifier}")
        srt_url = f"{BASE_URL}/download/{args.identifier}/{srt_file['name']}"
        srt_text = client.get(srt_url).text

        video_file = None
        if args.video:
            video_file = next((f for f in files if f["name"] == args.video), None)
        if not video_file:
            video_file = pick_file(files, r"\.mp4$", preferred=["512kb", "h.264"])
        if not video_file:
            sys.exit(f"no mp4 in item {args.identifier}")
        video_url = f"{BASE_URL}/download/{args.identifier}/{video_file['name']}"

    segments = parse_srt(srt_text)
    if not segments:
        sys.exit("no usable dialogue segments in subtitle file")
    print(f"Item: {args.identifier} | title: {title}")
    print(f"Video: {video_file['name']} ({round(float(video_file.get('size') or 0) / 1e6)} MB)")
    print(f"Subtitles: {srt_file['name']} | {len(segments)} segments")

    if args.dry_run:
        for s in segments[:10]:
            print(f"  {s['start']:8.2f}-{s['end']:8.2f}  {s['text'][:70]}")
        return

    from pymongo import MongoClient

    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=10000)
    db = client[settings.mongodb_database]

    old = db.videos.find_one({"sourceUrl": video_url})
    if old:
        print(f"Removing previous index of this item (video {old['_id']})")
        db.dialogue.delete_many({"videoId": str(old["_id"])})
        db.videos.delete_one({"_id": old["_id"]})

    video_id = db.videos.insert_one(
        {
            "title": title,
            "source": "archive.org",
            "fileUrl": f"media/source/{video_file['name']}",
            "sourceUrl": video_url,
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
        }
    ).inserted_id

    inserted = 0
    for s in segments:
        character = "Unknown"
        text = s["text"]
        m = SPEAKER_RE.match(text)
        if m:
            character, text = m.group(1), m.group(2)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        db.dialogue.insert_one(
            {
                "videoId": str(video_id),
                "character": character,
                "text": text,
                "startTime": s["start"],
                "endTime": s["end"],
                "confidence": 0.9,
                "tokenCount": len(tokenize(text)),
                "normalizedText": normalize_text(text),
            }
        )
        inserted += 1

    print(f"Indexed {inserted} dialogue segments for \"{title}\".")
    print("The film file is fetched from archive.org automatically on first use.")


if __name__ == "__main__":
    main()
