"""FFmpeg wrappers for clip extraction and concatenation.

All commands run as subprocesses with explicit argument lists (no shell),
so paths with spaces are safe. Clip extraction always re-encodes to
identical parameters (H.264 + AAC, yuv420p, faststart) so that
concatenation can stream-copy without a re-encode.
"""

import asyncio
import json
import shutil
from pathlib import Path

from ..config import settings


class FFmpegError(Exception):
    pass


def find_ffmpeg() -> str:
    exe = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not exe:
        raise FFmpegError(
            "ffmpeg not found. Install it (e.g. `winget install Gyan.FFmpeg`) "
            "or set FFMPEG_PATH in backend/.env."
        )
    return exe


def find_ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe and settings.ffmpeg_path:
        exe = str(Path(settings.ffmpeg_path).with_name("ffprobe.exe"))
        if not Path(exe).is_file():
            exe = str(Path(settings.ffmpeg_path).with_name("ffprobe"))
    if not exe or not shutil.which(exe) and not Path(exe).is_file():
        raise FFmpegError("ffprobe not found next to ffmpeg.")
    return exe


async def probe_metadata(path: Path | str) -> dict:
    """Return {duration, width, height, fps} for a media file."""
    exe = find_ffprobe()
    proc = await asyncio.create_subprocess_exec(
        exe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError("ffprobe failed:\n" + err.decode(errors="replace")[-1000:])
    data = json.loads(out.decode(errors="replace"))
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    fps = None
    if video_stream and video_stream.get("r_frame_rate"):
        num, _, den = video_stream["r_frame_rate"].partition("/")
        den = float(den) if den else 0.0
        fps = round(float(num) / den) if den else None
    return {
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
        "width": int(video_stream["width"]) if video_stream and video_stream.get("width") else None,
        "height": int(video_stream["height"]) if video_stream and video_stream.get("height") else None,
        "fps": fps,
    }


async def _run(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace").strip().splitlines()[-15:]
        raise FFmpegError("ffmpeg failed:\n" + "\n".join(tail))


_UA = "Mozilla/5.0 (compatible; RepliqDialogue/1.0; +https://hellenicdev.eu)"


async def extract_clip(input_path: Path | str, start: float, end: float, output_path: Path) -> Path:
    """Extract [start, end) from the input video into a normalized clip.

    The input may be a local path or a URL; ffmpeg seeks with range
    requests over HTTP, so remote films are cut without downloading the
    whole file.
    """
    duration = max(0.05, end - start)
    vf = (
        f"scale={settings.output_width}:{settings.output_height}:force_original_aspect_ratio=decrease,"
        f"pad={settings.output_width}:{settings.output_height}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={settings.output_fps}"
    )
    args = [
        find_ffmpeg(), "-y",
        "-ss", f"{start:.3f}",
        "-accurate_seek",
        "-t", f"{duration:.3f}",
    ]
    if isinstance(input_path, str):
        args += ["-user_agent", _UA, "-reconnect", "1", "-reconnect_delay_max", "5"]
    args += [
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    ]
    await _run(args)
    return output_path


async def concat_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """Concatenate pre-normalized clips with the concat demuxer (stream copy)."""
    if not clip_paths:
        raise FFmpegError("no clips to concatenate")

    list_path = output_path.with_suffix(".txt")
    lines = []
    for p in clip_paths:
        safe = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        args = [
            find_ffmpeg(), "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        await _run(args)
    finally:
        list_path.unlink(missing_ok=True)
    return output_path
