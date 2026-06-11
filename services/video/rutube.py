from __future__ import annotations

import asyncio
import re
import shutil
import sys
from pathlib import Path

import structlog

from config.settings import settings
from services.highlights.schemas import VideoContext
from services.video.validation import VideoValidationError

logger = structlog.get_logger(__name__)

RUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/[\w-]+",
    re.IGNORECASE,
)


def extract_rutube_url(text: str) -> str | None:
    """Извлекает URL Rutube из текста (поддерживает private и текст вокруг ссылки)."""
    cleaned = text.strip()
    match = RUTUBE_URL_RE.search(cleaned)
    if match:
        return match.group(0).rstrip("/")
    # rutube.ru/video/xxx без схемы
    loose = re.search(
        r"(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/[\w-]+",
        cleaned,
        re.IGNORECASE,
    )
    if loose:
        return f"https://{loose.group(0).rstrip('/')}"
    return None


def _max_filesize_arg() -> str:
    limit = settings.max_rutube_download_bytes
    if limit >= 1024**3:
        return f"{limit // (1024**3)}G"
    if limit >= 1024**2:
        return f"{limit // (1024**2)}M"
    return f"{limit // 1024}K"


def _ytdlp_command() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


async def fetch_rutube_metadata(url: str) -> VideoContext:
    args = [
        *_ytdlp_command(),
        "--no-playlist",
        "--no-warnings",
        "--print",
        "%(title)s",
        "--print",
        "%(description)s",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
    title = lines[0].strip() if lines else ""
    description = lines[1].strip() if len(lines) > 1 else ""
    return VideoContext(title=title, description=description)


async def download_rutube_video(url: str, destination: Path) -> Path:
    if not settings.rutube_enabled:
        raise VideoValidationError("Загрузка с Rutube отключена.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(destination.with_suffix(".%(ext)s"))
    max_duration = settings.max_video_duration_sec
    height = settings.rutube_max_height

    args = [
        *_ytdlp_command(),
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
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoValidationError("Скачивание с Rutube заняло слишком много времени. Попробуйте позже.")
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
    if "format" in lower or "filter specification" in lower:
        return "Не удалось выбрать формат видео на Rutube. Попробуйте позже."
    return "Не удалось скачать видео с Rutube. Проверьте ссылку и доступность ролика."
