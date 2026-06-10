from __future__ import annotations

from pathlib import Path

from services.video.ffmpeg import run_ffprobe


async def get_video_rotation(path: Path) -> int:
    """Градусы поворота из метаданных (iPhone/Android часто rotate=90)."""
    probe = await run_ffprobe(path)
    stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        return int(float(tags["rotate"])) % 360

    for item in stream.get("side_data_list") or []:
        if item.get("side_data_type") == "Display Matrix" and "rotation" in item:
            # Display Matrix: отрицательное значение = поворот по часовой для отображения
            return int(-float(item["rotation"])) % 360

    return 0


def rotation_vf_prefix(degrees: int) -> str:
    """ffmpeg-фильтры для «запекания» поворота в пиксели."""
    if degrees == 90:
        return "transpose=1,"
    if degrees == 180:
        return "hflip,vflip,"
    if degrees == 270:
        return "transpose=2,"
    return ""
