from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

import structlog

from config.settings import settings
from services.video.validation import VideoValidationError

logger = structlog.get_logger(__name__)

RUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?rutube\.ru/(?:video|play/embed|shorts)/[\w-]+/?",
    re.IGNORECASE,
)


def extract_rutube_url(text: str) -> str | None:
    match = RUTUBE_URL_RE.search(text.strip())
    return match.group(0).rstrip("/") if match else None


def _max_filesize_arg() -> str:
    limit = settings.max_rutube_download_bytes
    if limit >= 1024**3:
        return f"{limit // (1024**3)}G"
    if limit >= 1024**2:
        return f"{limit // (1024**2)}M"
    return f"{limit // 1024}K"


async def download_rutube_video(url: str, destination: Path) -> Path:
    if not settings.rutube_enabled:
        raise VideoValidationError("Загрузка с Rutube отключена.")

    binary = shutil.which("yt-dlp")
    if not binary:
        raise VideoValidationError(
            "yt-dlp не установлен на сервере. Обратитесь к администратору."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(destination.with_suffix(".%(ext)s"))
    max_duration = settings.max_video_duration_sec
    height = settings.rutube_max_height

    args = [
        binary,
        "--no-playlist",
        "--no-warnings",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--max-filesize",
        _max_filesize_arg(),
        "--match-filter",
        f"duration <= {max_duration}",
        "-f",
        f"bv*[height<={height}]+ba/b[height<={height}]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
        url,
    ]

    logger.info("rutube_download_start", url=url)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stderr or stdout or b"").decode("utf-8", errors="replace")

    if proc.returncode != 0:
        logger.warning("rutube_download_failed", url=url, output=output[-2000:])
        raise VideoValidationError(_map_ytdlp_error(output))

    if destination.exists():
        return destination

    for candidate in destination.parent.glob(f"{destination.stem}.*"):
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
            candidate.rename(destination)
            return destination

    raise VideoValidationError("Не удалось скачать видео с Rutube.")


def _map_ytdlp_error(output: str) -> str:
    lower = output.lower()
    if "private" in lower or "sign in" in lower:
        return "Видео недоступно (приватное или требует авторизации)."
    if "geo" in lower or "not available" in lower:
        return "Видео недоступно в вашем регионе или удалено."
    if "filesize" in lower or "too large" in lower:
        limit_mb = settings.max_rutube_download_bytes / (1024 * 1024)
        return f"Видео слишком большое (лимит {limit_mb:.0f} МБ)."
    if "duration" in lower:
        return f"Видео длиннее {settings.max_video_duration_sec // 60} минут."
    return "Не удалось скачать видео с Rutube. Проверьте ссылку и доступность ролика."
