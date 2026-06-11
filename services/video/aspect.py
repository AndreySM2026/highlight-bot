from __future__ import annotations

import re

from services.video.ffmpeg import run_ffmpeg

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

_CROPDETECT_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


async def detect_content_crop(input_path) -> tuple[int, int, int, int] | None:
    """Убирает вшитые чёрные полосы (letterbox), если они есть в исходнике."""
    output = await run_ffmpeg(
        [
            "-t",
            "45",
            "-i",
            str(input_path),
            "-vf",
            "cropdetect=limit=24:round=2:reset=0",
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


def build_vertical_916_filter(
    *,
    rotation_prefix: str = "",
    content_crop: tuple[int, int, int, int] | None = None,
) -> str:
    """
    Строгий cover-crop 9:16 → ровно 1080×1920, SAR=1, без letterbox/pillarbox.
    """
    w, h = TARGET_WIDTH, TARGET_HEIGHT
    crop_prefix = ""
    if content_crop:
        cw, ch, cx, cy = content_crop
        crop_prefix = f"crop={cw}:{ch}:{cx}:{cy},"
    # Фиксированный crop 1080×1920 — динамические w/h/x/y ломаются в ffmpeg (Eval: 'w'/2).
    return (
        f"{rotation_prefix}"
        f"{crop_prefix}"
        "scale=iw*sar:ih:flags=lanczos,"
        "setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,"
        f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=disable,"
        "setsar=1"
    )
