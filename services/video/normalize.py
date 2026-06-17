from __future__ import annotations

from pathlib import Path

import structlog

from config.settings import settings
from services.video.ffmpeg import run_ffmpeg, run_ffprobe
from services.video.rotation import get_video_rotation, rotation_vf_prefix

logger = structlog.get_logger(__name__)


def _parse_fps(rate: str | None) -> float:
    if not rate or rate in {"0/0", "0"}:
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        denominator = float(den)
        return float(num) / denominator if denominator else 0.0
    return float(rate)


async def _video_stream_info(path: Path) -> dict:
    probe = await run_ffprobe(path)
    video = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )
    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    return {
        "duration": float(probe.get("format", {}).get("duration", 0)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": fps,
        "vcodec": (video.get("codec_name") or "").lower(),
        "acodec": (audio.get("codec_name") or "").lower(),
    }


def _can_remux(info: dict, rotation: int) -> bool:
    if rotation != 0:
        return False
    if info["vcodec"] != "h264":
        return False
    if info["acodec"] not in {"aac", "mp3"}:
        return False
    if info["fps"] and not (15 <= info["fps"] <= 60):
        return False
    return True


def _normalize_timeout(duration_sec: float) -> float:
    return min(7200.0, max(120.0, duration_sec * 2.0))


async def normalize_video(input_path: Path, output_path: Path) -> dict:
    """
    Готовит файл для анализа активности.
    Rutube/mp4 h264 чаще всего remux без перекодирования (секунды).
    Иначе — лёгкий прокси 720p veryfast (не полный 1080p re-encode).
    Клипы рендерятся из input.mp4 в полном качестве.
    """
    size_mb = input_path.stat().st_size / (1024 * 1024)
    rotation = await get_video_rotation(input_path)
    info = await _video_stream_info(input_path)
    logger.info(
        "normalize_start",
        path=str(input_path),
        size_mb=round(size_mb, 1),
        duration=round(info["duration"], 1),
        vcodec=info["vcodec"],
        height=info["height"],
        rotation=rotation,
    )

    if _can_remux(info, rotation):
        await run_ffmpeg(
            [
                "-i",
                str(input_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            label="normalize_remux",
            timeout=min(120.0, _normalize_timeout(info["duration"])),
        )
        logger.info("normalize_done", mode="remux", path=str(output_path))
        return {
            "duration": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "fps": info["fps"] or 30.0,
            "mode": "remux",
        }

    rotate_vf = rotation_vf_prefix(rotation)
    max_h = settings.analysis_max_height
    vf = (
        f"{rotate_vf}"
        f"fps=30,"
        f"scale=-2:'min({max_h},ih)',"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        f"setsar=1"
    )
    timeout = _normalize_timeout(info["duration"])
    await run_ffmpeg(
        [
            "-threads",
            "0",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-c:v",
            "libx264",
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
            str(output_path),
        ],
        label="normalize",
        timeout=timeout,
    )
    probe = await run_ffprobe(output_path)
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    logger.info(
        "normalize_done",
        mode="reencode",
        path=str(output_path),
        height=int(video_stream.get("height", 0)),
    )
    return {
        "duration": float(probe.get("format", {}).get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": 30,
        "mode": "reencode",
    }
