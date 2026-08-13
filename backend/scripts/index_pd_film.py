"""Index a public-domain film from the Internet Archive into MongoDB.

Downloads nothing heavy: only the item's metadata API is queried. The
dialogue segments come from either

  a) the item's subtitle file (ASR SRT) when one exists, or
  b) a fresh Groq Whisper transcription of the film's audio (--transcribe) —
     this unlocks famous films that archive.org carries without subtitles.

The actual video is fetched lazily by the backend the first time a
generation needs a clip from it.

Usage (from backend/):
    python scripts/index_pd_film.py --identifier CarnivalOfSouls1962
    python scripts/index_pd_film.py --identifier <id> --video <file.mp4> --srt <file.srt> --title "Name"
    python scripts/index_pd_film.py --identifier <id> --transcribe   # no SRT on archive.org
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
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
CUE_RE = re.compile(r"^[(\[]?(music|applause|laughter|typing|whistling|screaming|man|woman|speech)[)\]]?$", re.I)
SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z .'\-]+):\s*(.+)$")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_CHUNK_SECONDS = 1200  # keeps uploads ~38 MB (wav 16 kHz mono) under Groq's 100 MB cap


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


def _ffmpeg() -> str:
    path = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not path:
        sys.exit("ffmpeg not found: set FFMPEG_PATH in backend/.env")
    return path


def extract_audio_chunks(video_path: Path, workdir: Path) -> list[Path]:
    """Extract 16 kHz mono WAV chunks (WHISPER_CHUNK_SECONDS each) from a video file."""
    pattern = workdir / "chunk_%03d.wav"
    cmd = [
        _ffmpeg(), "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-f", "segment", "-segment_time", str(WHISPER_CHUNK_SECONDS),
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg audio extraction failed:\n{result.stderr[-2000:]}")
    chunks = sorted(workdir.glob("chunk_*.wav"))
    if not chunks:
        sys.exit("no audio extracted from video")
    return chunks


def transcribe_chunk(client: httpx.Client, chunk: Path, model: str, offset: float) -> list[dict]:
    data = {"model": model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
    files = {"file": ("chunk.wav", chunk.open("rb"), "audio/wav")}
    resp = client.post(GROQ_URL, data=data, files=files, timeout=600)
    if resp.status_code != 200:
        raise RuntimeError(f"Groq transcription failed (HTTP {resp.status_code}): {resp.text[:300]}")
    payload = resp.json()
    out = []
    for seg in payload.get("segments", []):
        text = clean_text(seg.get("text", ""))
        if not text:
            continue
        start = float(seg["start"]) + offset
        end = float(seg["end"]) + offset
        if end - start < 0.4 or end - start > 20:
            continue
        avg_logprob = seg.get("avg_logprob")
        confidence = round(2 ** avg_logprob, 3) if avg_logprob is not None else 0.9
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text, "confidence": confidence})
    return out


def transcribe_and_parse(video_url: str, model: str) -> list[dict]:
    """Download the film, extract audio, transcribe with Groq Whisper, return segments."""
    print(f"  downloading {video_url} for transcription...")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        video_path = workdir / "film.mp4"
        with httpx.stream("GET", video_url, follow_redirects=True, timeout=httpx.Timeout(60, read=600)) as resp:
            if resp.status_code != 200:
                sys.exit(f"failed to download {video_url} (HTTP {resp.status_code})")
            with video_path.open("wb") as fh:
                for chunk in resp.iter_bytes(1024 * 1024):
                    fh.write(chunk)

        chunks = extract_audio_chunks(video_path, workdir)

        segments: list[dict] = []
        with httpx.Client(timeout=600) as client:
            for i, chunk in enumerate(chunks):
                offset = i * WHISPER_CHUNK_SECONDS
                segs = transcribe_chunk(client, chunk, model, offset)
                print(f"  chunk {i + 1}/{len(chunks)} -> {len(segs)} segments")
                segments.extend(segs)
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
    parser.add_argument("--transcribe", action="store_true", help="transcribe the film audio with Groq Whisper instead of using an SRT")
    parser.add_argument("--whisper-model", default="whisper-large-v3", help="Groq Whisper model (default whisper-large-v3)")
    parser.add_argument("--dry-run", action="store_true", help="only print what would be indexed")
    args = parser.parse_args()

    with httpx.Client(timeout=30) as client:
        meta = client.get(f"{BASE_URL}/metadata/{args.identifier}").json()
        if meta.get("error") or not meta.get("files"):
            sys.exit(f"item not found: {args.identifier}")
        files = meta["files"]
        title = args.title or meta.get("metadata", {}).get("title", args.identifier)

        video_file = None
        if args.video:
            video_file = next((f for f in files if f["name"] == args.video), None)
        if not video_file:
            video_file = pick_file(files, r"\.mp4$", preferred=["512kb", "h.264"])
        if not video_file:
            sys.exit(f"no mp4 in item {args.identifier}")
        video_url = f"{BASE_URL}/download/{args.identifier}/{video_file['name']}"

        if args.transcribe:
            segments = transcribe_and_parse(video_url, args.whisper_model)
            srt_name = f"groq {args.whisper_model}"
        else:
            srt_file = pick_file(files, r"\.(srt|vtt)$")
            if args.srt:
                srt_file = next((f for f in files if f["name"] == args.srt), None)
            if not srt_file:
                sys.exit(f"no subtitle file in item {args.identifier} (use --transcribe to transcribe it)")
            srt_url = f"{BASE_URL}/download/{args.identifier}/{srt_file['name']}"
            srt_text = client.get(srt_url).text
            segments = parse_srt(srt_text)
            srt_name = srt_file["name"]

    if not segments:
        sys.exit("no usable dialogue segments")

    print(f"Item: {args.identifier} | title: {title}")
    print(f"Video: {video_file['name']} ({round(float(video_file.get('size') or 0) / 1e6)} MB)")
    print(f"Subtitles: {srt_name} | {len(segments)} segments")

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
                "confidence": s.get("confidence", 0.9),
                "tokenCount": len(tokenize(text)),
                "normalizedText": normalize_text(text),
            }
        )
        inserted += 1

    print(f"Indexed {inserted} dialogue segments for \"{title}\".")
    print("The film file is fetched from archive.org automatically on first use.")


if __name__ == "__main__":
    main()
