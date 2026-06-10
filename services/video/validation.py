from __future__ import annotations

from pathlib import Path

from config.constants import ALLOWED_VIDEO_EXTENSIONS, ALLOWED_VIDEO_MIME
from config.settings import settings
from services.video.ffmpeg import FFmpegError, run_ffprobe


class VideoValidationError(Exception):
    pass


def file_too_large_message(size_bytes: int, limit_bytes: int) -> str:
    limit_mb = limit_bytes / (1024 * 1024)
    if size_bytes > limit_bytes:
        size_mb = size_bytes / (1024 * 1024)
        head = f"⚠️ Файл слишком большой ({size_mb:.0f} МБ).\n"
    else:
        head = "⚠️ Файл слишком большой.\n"
    return (
        f"{head}"
        f"Telegram Bot API позволяет скачать до {limit_mb:.0f} МБ.\n\n"
        "Сожмите видео (HandBrake, iMovie, онлайн-компрессор) "
        "или отправьте более короткий ролик."
    )


async def validate_video(path: Path, mime_type: str | None = None) -> float:
    suffix = path.suffix.lower()
    if mime_type and mime_type not in ALLOWED_VIDEO_MIME:
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            raise VideoValidationError("Поддерживаются только видеоформаты mp4, mov, avi, mpeg.")

    try:
        probe = await run_ffprobe(path)
    except FFmpegError as exc:
        raise VideoValidationError("Не удалось прочитать видеофайл.") from exc

    duration = float(probe.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise VideoValidationError("Видео имеет нулевую длительность.")
    if duration > settings.max_video_duration_sec:
        raise VideoValidationError(
            f"Видео слишком длинное. Максимум {settings.max_video_duration_sec // 60} минут."
        )
    return duration
