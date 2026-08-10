"""Phase 1 test dataset: generates synthetic videos and seeds MongoDB.

Each video is a solid-color MP4 with the dialogue line burned in as text and
a short audio tone, so timestamps are exactly known and the whole pipeline
(extract -> concat -> MP4 with audio) is exercised. Fully legal: nothing is
copied from any copyrighted source.

Usage (from backend/):
    python scripts/seed_test_data.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.utils.text import normalize_text, tokenize  # noqa: E402

WIDTH, HEIGHT, FPS = 640, 360, 24

LINES = [
    {"text": "I don't know.", "duration": 4.0, "color": "0x2E86AB", "frequency": 392, "character": "Character A"},
    {"text": "What are you talking about?", "duration": 5.0, "color": "0xB03A2E", "frequency": 440, "character": "Character B"},
    {"text": "We need to leave.", "duration": 4.0, "color": "0x1E8449", "frequency": 494, "character": "Character C"},
    {"text": "Right now.", "duration": 3.0, "color": "0xD68910", "frequency": 523, "character": "Character D"},
    {"text": "Come with me.", "duration": 3.0, "color": "0x6C3483", "frequency": 587, "character": "Character E"},
    {"text": "Wait a minute.", "duration": 3.0, "color": "0x148F77", "frequency": 659, "character": "Character F"},
    {"text": "Let's go.", "duration": 3.0, "color": "0x7B241C", "frequency": 698, "character": "Character G"},
]

FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
]

# Font copied into media/fonts with a relative, colon-free path so the
# drawtext filter needs no quoting or escaping (robust across shells).
FONT_REL = Path("media/fonts/drawtext.ttf")


def prepare_font() -> None:
    for p in FONT_CANDIDATES:
        if p.exists():
            FONT_REL.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, FONT_REL)
            return


def drawtext_args() -> str:
    return f"fontfile={FONT_REL.as_posix()}:fontsize=36:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"


def generate_video(index: int, line: dict, out_path: Path) -> None:
    # Relative textfile path: no colons to escape, works regardless of machine.
    text_file = Path("media/source") / f"{out_path.stem}.txt"
    text_file.write_text(line["text"], encoding="utf-8")

    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg not found. Install it (`winget install Gyan.FFmpeg`) or set FFMPEG_PATH in .env")

    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c={line['color']}:s={WIDTH}x{HEIGHT}:d={line['duration']}",
        "-f", "lavfi", "-i", f"sine=frequency={line['frequency']}:duration={line['duration']}",
        "-filter_complex",
        f"[0:v]drawtext=textfile={text_file.as_posix()}:{drawtext_args()}[v]",
        "-map", "[v]",
        "-map", "1:a",
        "-af", "volume=0.3",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"failed to generate {out_path.name}:\n{result.stderr[-2000:]}")
    text_file.unlink(missing_ok=True)
    print(f"  generated {out_path.name}")


def main() -> None:
    from pymongo import MongoClient

    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=10000)
    db = client[settings.mongodb_database]

    print("Clearing existing test data...")
    db.videos.delete_many({"source": "local"})
    db.dialogue.delete_many({})
    db.jobs.delete_many({})
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    for f in list(settings.clips_dir.glob("*.mp4")) + list(settings.output_dir.glob("*.mp4")):
        f.unlink(missing_ok=True)

    print("Generating synthetic videos...")
    prepare_font()
    for i, line in enumerate(LINES, start=1):
        file_name = f"seed_video_{i}.mp4"
        out_path = settings.source_dir / file_name
        if not out_path.is_file():
            generate_video(i, line, out_path)

        video_id = db.videos.insert_one(
            {
                "title": f"Test Video {i}",
                "source": "local",
                "fileUrl": f"media/source/{file_name}",
                "duration": line["duration"],
                "width": WIDTH,
                "height": HEIGHT,
                "fps": FPS,
            }
        ).inserted_id

        # Dialogue starts 0.8s in; text file naming keeps timestamps exact.
        start = 0.8
        end = line["duration"] - 0.4
        tokens = tokenize(line["text"])
        db.dialogue.insert_one(
            {
                "videoId": str(video_id),
                "character": line["character"],
                "text": line["text"],
                "startTime": round(start, 3),
                "endTime": round(end, 3),
                "confidence": 0.99,
                "tokenCount": len(tokens),
                "normalizedText": normalize_text(line["text"]),
            }
        )

    videos = db.videos.count_documents({"source": "local"})
    dialogue = db.dialogue.count_documents({})
    print(f"Done. {videos} videos, {dialogue} dialogue segments.")
    print('Try: "We need to leave right now" or "I don\'t know what you\'re talking about".')


if __name__ == "__main__":
    main()
