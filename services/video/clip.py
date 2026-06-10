from __future__ import annotations

from pathlib import Path

from config.settings import settings
from services.highlights.schemas import HighlightSegment
from services.video.ffmpeg import run_ffmpeg, run_ffprobe


def build_vertical_916_filter(
    width: int | None = None,
    height: int | None = None,
) -> str:
    """
    Cover-crop в 9:16 без растягивания: масштаб по большей стороне, затем обрезка по центру.
    """
    w = width or settings.target_width
    h = height or settings.target_height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h},"
        f"setsar=1"
    )


async def render_clip(
    input_path: Path,
    segment: HighlightSegment,
    output_path: Path,
) -> Path:
    crop_filter = build_vertical_916_filter()
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
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata:s:v:0",
            "rotate=0",
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
            "-vf",
            "setsar=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
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
