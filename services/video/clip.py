from __future__ import annotations

from pathlib import Path

from config.settings import settings
from services.highlights.schemas import HighlightSegment
from services.video.ffmpeg import run_ffmpeg, run_ffprobe
from services.video.rotation import get_video_rotation, rotation_vf_prefix


def build_vertical_916_filter(
    width: int | None = None,
    height: int | None = None,
    *,
    rotation_prefix: str = "",
) -> str:
    """
    Cover-crop в 9:16 без растягивания.
    Сначала выравниваем SAR (Rutube/mp4 часто anamorphic), затем crop по центру.
    """
    w = width or settings.target_width
    h = height or settings.target_height
    return (
        f"{rotation_prefix}"
        f"scale=iw*sar:ih,setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,"
        f"scale={w}:{h},"
        f"setsar=1"
    )


async def render_clip(
    input_path: Path,
    segment: HighlightSegment,
    output_path: Path,
) -> Path:
    rotation = await get_video_rotation(input_path)
    crop_filter = build_vertical_916_filter(rotation_prefix=rotation_vf_prefix(rotation))
    await run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-ss",
            str(segment.start_time),
            "-to",
            str(segment.end_time),
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
            "-map_metadata",
            "-1",
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
            "scale=iw*sar:ih,setsar=1",
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
            "-map_metadata",
            "-1",
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
