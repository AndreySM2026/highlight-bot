from __future__ import annotations

import re
from pathlib import Path

import structlog

from services.video.ffmpeg import run_ffmpeg, run_ffprobe
from services.video.rotation import get_video_rotation

logger = structlog.get_logger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

_CROPDETECT_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def _parse_sar(stream: dict) -> float:
    raw = stream.get("sample_aspect_ratio", "1:1")
    if not raw or raw in {"0:0", "N/A", "unknown"}:
        return 1.0
    if "/" in raw:
        num, den = raw.split("/", 1)
    elif ":" in raw:
        num, den = raw.split(":", 1)
    else:
        return 1.0
    denominator = float(den)
    return float(num) / denominator if denominator else 1.0


def _even(value: float) -> int:
    iv = int(value)
    return iv - iv % 2


def _letterbox_heuristic(display_w: float, display_h: float) -> tuple[int, int, int, int] | None:
    """
    Убирает вшитые чёрные полосы сверху/снизу (16:9 внутри 9:16 контейнера).
    """
    if display_h <= display_w * (9 / 16) * 1.06:
        return None

    content_h = _even(display_w * 9 / 16)
    if content_h <= 0 or content_h >= display_h * 0.98:
        return None

    y = _even((display_h - content_h) / 2)
    return _even(display_w), content_h, 0, y


async def _cropdetect_raw(input_path: Path) -> tuple[int, int, int, int] | None:
    output = await run_ffmpeg(
        [
            "-t",
            "60",
            "-i",
            str(input_path),
            "-vf",
            "cropdetect=limit=20:round=2:reset=0",
            "-f",
            "null",
            "-",
        ],
        label="cropdetect",
        timeout=120,
    )
    matches = _CROPDETECT_RE.findall(output)
    if not matches:
        return None
    w, h, x, y = (int(v) for v in matches[-1])
    if w <= 0 or h <= 0:
        return None
    return w, h, x, y


def _validate_letterbox_crop(
    crop: tuple[int, int, int, int],
    full_w: int,
    full_h: int,
) -> tuple[int, int, int, int] | None:
    cw, ch, cx, cy = crop
    if cw * ch >= full_w * full_h * 0.97:
        return None
    if ch / cw < 1.0:
        return crop
    if cw / ch >= 1.2:
        return crop
    return None


async def analyze_video_geometry(input_path: Path) -> dict:
    """Анализ кадра: поворот, letterbox, тип (landscape/portrait)."""
    rotation = await get_video_rotation(input_path)
    probe = await run_ffprobe(input_path)
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    full_w = int(stream.get("width", 0))
    full_h = int(stream.get("height", 0))
    sar = _parse_sar(stream)

    if rotation in {90, 270}:
        display_w, display_h = full_h * sar, full_w
    else:
        display_w, display_h = full_w * sar, full_h

    is_landscape = display_w > display_h * 1.12
    letterbox: tuple[int, int, int, int] | None = None

    if not is_landscape:
        letterbox = _letterbox_heuristic(display_w, display_h)
        if letterbox is None:
            try:
                detected = await _cropdetect_raw(input_path)
                if detected:
                    letterbox = _validate_letterbox_crop(detected, full_w, full_h)
            except Exception as exc:
                logger.warning("cropdetect_failed", error=str(exc))

    geometry = {
        "rotation": rotation,
        "full_w": full_w,
        "full_h": full_h,
        "sar": sar,
        "is_landscape": is_landscape,
        "letterbox": list(letterbox) if letterbox else None,
    }
    logger.info("video_geometry", path=str(input_path), **geometry)
    return geometry


def build_vertical_916_filter(
    *,
    rotation_prefix: str = "",
    letterbox: tuple[int, int, int, int] | None = None,
    is_landscape: bool = True,
) -> str:
    """
    Строго 1080×1920, cover-crop без чёрных полос и без растягивания.
    """
    w, h = TARGET_WIDTH, TARGET_HEIGHT
    prefix = rotation_prefix
    if letterbox:
        cw, ch, cx, cy = letterbox
        prefix += f"crop={cw}:{ch}:{cx}:{cy},"

    if is_landscape:
        # Центральный вертикальный 9:16 из landscape — без scale-to-fit (нет letterbox).
        return (
            f"{prefix}"
            "scale=iw*sar:ih:flags=lanczos,"
            "setsar=1,"
            "crop='trunc(ih*9/16/2)*2':ih:'(iw-trunc(ih*9/16/2)*2)/2':0,"
            f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=disable,"
            "setsar=1"
        )

    return (
        f"{prefix}"
        "scale=iw*sar:ih:flags=lanczos,"
        "setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,"
        f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=disable,"
        "setsar=1"
    )
