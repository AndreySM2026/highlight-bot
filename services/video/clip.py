from __future__ import annotations

from pathlib import Path

import structlog

from services.highlights.schemas import HighlightSegment
from services.video.aspect import TARGET_HEIGHT, TARGET_WIDTH, build_vertical_916_filter
from services.video.ffmpeg import FFmpegError, run_ffmpeg, run_ffprobe
from services.video.rotation import get_video_rotation, rotation_vf_prefix

logger = structlog.get_logger(__name__)


async def _assert_output_dimensions(path: Path) -> None:
    probe = await run_ffprobe(path)
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))
    sar = stream.get("sample_aspect_ratio", "1:1")
    if width != TARGET_WIDTH or height != TARGET_HEIGHT:
        raise FFmpegError(
            f"render_clip bad dimensions: {width}x{height} (expected {TARGET_WIDTH}x{TARGET_HEIGHT})"
        )
    if sar not in {"1:1", "1/1"}:
        logger.warning("render_clip_non_square_sar", path=str(path), sar=sar)


async def render_clip(
    input_path: Path,
    segment: HighlightSegment,
    output_path: Path,
    *,
    content_crop: tuple[int, int, int, int] | None = None,
) -> Path:
    rotation = await get_video_rotation(input_path)
    crop_filter = build_vertical_916_filter(
        rotation_prefix=rotation_vf_prefix(rotation),
        content_crop=content_crop,
    )
    logger.info(
        "render_clip_start",
        input=str(input_path),
        rotation=rotation,
        start=segment.start_time,
        end=segment.end_time,
    )
    await run_ffmpeg(
        [
            "-noautorotate",
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
            "-profile:v",
            "main",
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
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            str(output_path),
        ],
        label="render_clip",
    )
    await _assert_output_dimensions(output_path)
    return await compress_for_telegram(output_path)


async def compress_for_telegram(path: Path, max_bytes: int = 49 * 1024 * 1024) -> Path:
    if path.stat().st_size <= max_bytes:
        return path

    compressed = path.with_name(f"{path.stem}_compressed{path.suffix}")
    await run_ffmpeg(
        [
            "-noautorotate",
            "-i",
            str(path),
            "-vf",
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=disable,setsar=1",
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
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
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            str(compressed),
        ],
        label="compress_clip",
    )
    if compressed.exists() and compressed.stat().st_size > 0:
        await _assert_output_dimensions(compressed)
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
