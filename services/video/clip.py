from __future__ import annotations

from pathlib import Path

import structlog

from services.highlights.schemas import HighlightSegment
from services.video.aspect import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    build_vertical_916_filter,
    get_display_geometry,
)
from services.video.face_crop import (
    compute_crop_origin,
    detect_focus_point,
    needs_reframe,
    reframe_min_scale,
)
from services.video.ffmpeg import FFmpegError, run_ffmpeg, run_ffprobe

logger = structlog.get_logger(__name__)


def _render_timeout(clip_duration_sec: float) -> float:
    return min(600.0, max(120.0, clip_duration_sec * 6.0 + 60.0))


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


def _encode_args(*, video_filter: str | None) -> list[str]:
    args = [
        "-c:v",
        "libx264",
        "-profile:v",
        "main",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
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
    ]
    if video_filter:
        args = ["-vf", video_filter, *args]
    return args


async def _build_crop_filter(
    input_path: Path,
    segment: HighlightSegment,
    display,
) -> str | None:
    passthrough = display.is_exact_target()
    focus = await detect_focus_point(
        input_path,
        start_time=segment.start_time,
        duration=max(0.1, segment.end_time - segment.start_time),
        work_dir=input_path.parent,
    )

    if passthrough and (not focus or not needs_reframe(display, focus[0], focus[1])):
        return None

    if focus:
        min_scale = reframe_min_scale(display, focus[0], focus[1]) if passthrough else 1.0
        scale_mul, crop_x, crop_y = compute_crop_origin(
            display.display_width,
            display.display_height,
            focus[0],
            focus[1],
            min_scale=min_scale,
        )
        logger.info(
            "render_clip_face_crop",
            crop_x=crop_x,
            crop_y=crop_y,
            scale_mul=round(scale_mul, 3),
        )
        return build_vertical_916_filter(
            crop_x=crop_x,
            crop_y=crop_y,
            scale_mul=scale_mul,
            display_w=display.display_width,
            display_h=display.display_height,
        )

    return build_vertical_916_filter()


async def render_clip(
    input_path: Path,
    segment: HighlightSegment,
    output_path: Path,
    *,
    geometry: dict | None = None,
) -> Path:
    display = await get_display_geometry(input_path)
    crop_filter = await _build_crop_filter(input_path, segment, display)
    passthrough = crop_filter is None
    clip_duration = max(0.1, segment.end_time - segment.start_time)
    logger.info(
        "render_clip_start",
        input=str(input_path),
        passthrough=passthrough,
        display_width=round(display.display_width, 1),
        display_height=round(display.display_height, 1),
        rotation=display.rotation,
        filter=crop_filter,
        start=segment.start_time,
        end=segment.end_time,
        duration=round(clip_duration, 2),
    )
    await run_ffmpeg(
        [
            "-ss",
            str(segment.start_time),
            "-i",
            str(input_path),
            "-t",
            str(clip_duration),
            "-threads",
            "1",
            *_encode_args(video_filter=crop_filter),
            str(output_path),
        ],
        label="render_clip",
        timeout=_render_timeout(clip_duration),
    )
    await _assert_output_dimensions(output_path)
    return await compress_for_telegram(output_path)


async def compress_for_telegram(path: Path, max_bytes: int = 49 * 1024 * 1024) -> Path:
    if path.stat().st_size <= max_bytes:
        return path

    compressed = path.with_name(f"{path.stem}_compressed{path.suffix}")
    probe = await run_ffprobe(path)
    clip_duration = float(probe.get("format", {}).get("duration", 60))
    await run_ffmpeg(
        [
            "-i",
            str(path),
            "-threads",
            "1",
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
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
        timeout=_render_timeout(clip_duration),
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
