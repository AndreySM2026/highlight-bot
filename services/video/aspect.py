from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from services.video.ffmpeg import run_ffprobe
from services.video.rotation import get_video_rotation

logger = structlog.get_logger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_ASPECT = TARGET_WIDTH / TARGET_HEIGHT


def parse_sar(raw: str | None) -> float:
    if not raw or raw in {"0:0", "N/A", "unknown", "1:1", "1/1"}:
        return 1.0
    if "/" in raw:
        num, den = raw.split("/", 1)
    elif ":" in raw:
        num, den = raw.split(":", 1)
    else:
        return 1.0
    denominator = float(den)
    return float(num) / denominator if denominator else 1.0


@dataclass(frozen=True)
class DisplayGeometry:
    """Размер кадра после autorotate ffmpeg (как в -vf)."""

    coded_width: int
    coded_height: int
    display_width: float
    display_height: float
    sar: float
    rotation: int

    @property
    def aspect(self) -> float:
        if self.display_height <= 0:
            return 0.0
        return self.display_width / self.display_height

    def is_exact_target(self) -> bool:
        """Уже 1080×1920 в файле — можно нарезать без scale/crop."""
        if self.rotation != 0:
            return False
        if self.coded_width != TARGET_WIDTH or self.coded_height != TARGET_HEIGHT:
            return False
        return abs(self.sar - 1.0) < 0.01


def build_vertical_916_filter(
    *,
    crop_x: int | None = None,
    crop_y: int | None = None,
    scale_mul: float | None = None,
    display_w: float | None = None,
    display_h: float | None = None,
) -> str:
    """
    Instagram Stories / Reels: 1080×1920, cover-crop 9:16 без растягивания.

    crop_x/crop_y — смещение после scale-to-fill (для центровки по лицу).
    """
    w, h = TARGET_WIDTH, TARGET_HEIGHT
    if (
        crop_x is not None
        and crop_y is not None
        and scale_mul is not None
        and display_w
        and display_h
    ):
        base_scale = max(w / display_w, h / display_h)
        if scale_mul > base_scale * 1.001:
            sw = int(round(display_w * scale_mul))
            sh = int(round(display_h * scale_mul))
            sw += sw % 2
            sh += sh % 2
            return (
                f"scale={sw}:{sh}:flags=lanczos,"
                f"crop={w}:{h}:{crop_x}:{crop_y},"
                "setsar=1"
            )
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h}:{crop_x}:{crop_y},"
            "setsar=1"
        )

    x_expr = f"(iw-{w})/2"
    y_expr = f"(ih-{h})/2"
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h}:{x_expr}:{y_expr},"
        "setsar=1"
    )


async def get_display_geometry(input_path: Path) -> DisplayGeometry:
    rotation = await get_video_rotation(input_path)
    probe = await run_ffprobe(input_path)
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    coded_w = int(stream.get("width", 0))
    coded_h = int(stream.get("height", 0))
    sar = parse_sar(stream.get("sample_aspect_ratio"))

    if rotation in {90, 270}:
        display_w = coded_h * sar
        display_h = coded_w
    else:
        display_w = coded_w * sar
        display_h = coded_h

    return DisplayGeometry(
        coded_width=coded_w,
        coded_height=coded_h,
        display_width=display_w,
        display_height=display_h,
        sar=sar,
        rotation=rotation,
    )


async def analyze_video_geometry(input_path: Path) -> dict:
    """Диагностика кадра для логов (не влияет на рендер)."""
    geom = await get_display_geometry(input_path)
    geometry = {
        "rotation": geom.rotation,
        "width": geom.coded_width,
        "height": geom.coded_height,
        "display_width": round(geom.display_width, 1),
        "display_height": round(geom.display_height, 1),
        "aspect": round(geom.aspect, 4),
        "sar": geom.sar,
        "is_exact_target": geom.is_exact_target(),
    }
    logger.info("video_geometry", path=str(input_path), **geometry)
    return geometry
