from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import structlog

from config.settings import settings
from services.video.aspect import DisplayGeometry, TARGET_HEIGHT, TARGET_WIDTH
from services.video.ffmpeg import run_ffmpeg

logger = structlog.get_logger(__name__)

_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_UPPERBODY_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_upperbody.xml"
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def compute_crop_origin(
    display_w: float,
    display_h: float,
    focus_x: float,
    focus_y: float,
    *,
    min_scale: float = 1.0,
) -> tuple[float, int, int]:
    """
    Cover-scale до 9:16 и crop 1080×1920 с фокусом на точке (focus_x, focus_y).
    Возвращает (множитель scale, crop_x, crop_y).
    """
    tw, th = TARGET_WIDTH, TARGET_HEIGHT
    scale = max(tw / display_w, th / display_h, min_scale)
    scaled_w = display_w * scale
    scaled_h = display_h * scale
    focus_sx = focus_x * scale
    focus_sy = focus_y * scale
    crop_x = _clamp(round(focus_sx - tw / 2), 0, max(0, round(scaled_w - tw)))
    crop_y = _clamp(round(focus_sy - th / 2), 0, max(0, round(scaled_h - th)))
    return scale, int(crop_x), int(crop_y)


def _detect_focus_points(image_path: Path) -> list[tuple[float, float, float]]:
    image = cv2.imread(str(image_path))
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    points: list[tuple[float, float, float]] = []

    for cascade, weight in ((_FACE_CASCADE, 1.0), (_UPPERBODY_CASCADE, 0.6)):
        if cascade.empty():
            continue
        detections = cascade.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=4,
            minSize=(48, 48),
        )
        for x, y, w, h in detections:
            points.append((x + w / 2, y + h / 2, w * h * weight))

    return points


async def _extract_frame(video_path: Path, timestamp: float, output_path: Path) -> None:
    await run_ffmpeg(
        [
            "-ss",
            str(max(0.0, timestamp)),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ],
        label="face_frame",
        timeout=45,
    )


async def detect_focus_point(
    video_path: Path,
    *,
    start_time: float,
    duration: float,
    work_dir: Path,
) -> tuple[float, float] | None:
    if not settings.face_crop_enabled:
        return None

    timestamps = [
        start_time + duration * ratio
        for ratio in (0.2, 0.5, 0.8)
        if duration > 0.3
    ]
    if not timestamps:
        timestamps = [start_time]

    weighted_x = 0.0
    weighted_y = 0.0
    total_weight = 0.0

    for idx, timestamp in enumerate(timestamps):
        frame_path = work_dir / f"_focus_{idx}.jpg"
        try:
            await _extract_frame(video_path, timestamp, frame_path)
            points = await asyncio.to_thread(_detect_focus_points, frame_path)
            for cx, cy, area in points:
                weighted_x += cx * area
                weighted_y += cy * area
                total_weight += area
        except Exception as exc:
            logger.warning("face_frame_failed", timestamp=timestamp, error=str(exc))
        finally:
            frame_path.unlink(missing_ok=True)

    if total_weight <= 0:
        return None

    focus = (weighted_x / total_weight, weighted_y / total_weight)
    logger.info(
        "face_focus_detected",
        x=round(focus[0], 1),
        y=round(focus[1], 1),
        samples=len(timestamps),
    )
    return focus


def needs_reframe(geometry: DisplayGeometry, focus_x: float, focus_y: float) -> bool:
    """Лицо заметно смещено от центра — нужен crop даже для 1080×1920."""
    if not geometry.is_exact_target():
        return True
    cx = geometry.display_width / 2
    cy = geometry.display_height / 2
    dx = abs(focus_x - cx) / max(geometry.display_width, 1)
    dy = abs(focus_y - cy) / max(geometry.display_height, 1)
    return dx > 0.12 or dy > 0.10


def reframe_min_scale(geometry: DisplayGeometry, focus_x: float, focus_y: float) -> float:
    """Лёгкий зум для 9:16, чтобы сдвинуть кадр к лицу."""
    if geometry.is_exact_target():
        return 1.2
    return 1.0
