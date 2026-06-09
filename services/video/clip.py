from __future__ import annotations

from pathlib import Path

from config.settings import settings
from services.highlights.schemas import HighlightSegment
from services.video.ffmpeg import run_ffmpeg, run_ffprobe


async def render_clip(
    input_path: Path,
    segment: HighlightSegment,
    output_path: Path,
) -> Path:
    crop_filter = (
        f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={settings.target_width}:{settings.target_height}"
    )
    await run_ffmpeg(
        [
            "-ss",
            str(segment.start_time),
            "-to",
            str(segment.end_time),
            "-i",
            str(input_path),
            "-vf",
            crop_filter,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ],
        label="render_clip",
    )
    return await compress_for_telegram(output_path)


async def compress_for_telegram(path: Path, max_bytes: int = 49 * 1024 * 1024) -> Path:
    if path.stat().st_size <= max_bytes:
        return path

    compressed = path.with_name(f"{path.stem}_compressed{path.suffix}")
    await run_ffmpeg(
        [
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-fs",
            str(max_bytes),
            str(compressed),
        ],
        label="compress_clip",
    )
    if compressed.exists() and compressed.stat().st_size > 0:
        path.unlink(missing_ok=True)
        return compressed
    return path


async def get_video_meta(path: Path) -> dict:
    probe = await run_ffprobe(path)
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    return {
        "duration": float(probe.get("format", {}).get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
    }
