from __future__ import annotations

from pathlib import Path

import structlog

from services.video.ffmpeg import run_ffprobe
from services.video.rotation import get_video_rotation

logger = structlog.get_logger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


def build_vertical_916_filter() -> str:
    """
    Instagram Stories / Reels: 1080×1920, cover-crop 9:16 без растягивания.

    1. Выравниваем пиксели (SAR → 1)
    2. Масштаб «cover» — заполняет 9:16, лишнее обрежется
    3. Центральный crop ровно 1080×1920

    Поворот из метаданных (iPhone) ffmpeg применяет автоматически — без transpose.
    """
    w, h = TARGET_WIDTH, TARGET_HEIGHT
    return (
        "scale=iw*sar:ih,setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h},"
        "setsar=1"
    )


async def analyze_video_geometry(input_path: Path) -> dict:
    """Диагностика кадра для логов (не влияет на рендер)."""
    rotation = await get_video_rotation(input_path)
    probe = await run_ffprobe(input_path)
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    geometry = {
        "rotation": rotation,
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "sar": stream.get("sample_aspect_ratio", "1:1"),
    }
    logger.info("video_geometry", path=str(input_path), **geometry)
    return geometry
