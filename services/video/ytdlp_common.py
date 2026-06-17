from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import structlog

from config.settings import settings
from services.highlights.schemas import VideoContext
from services.video.validation import VideoValidationError

logger = structlog.get_logger(__name__)


def ytdlp_command() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


def max_filesize_arg() -> str:
    limit = settings.max_remote_download_bytes
    if limit >= 1024**3:
        return f"{limit // (1024**3)}G"
    if limit >= 1024**2:
        return f"{limit // (1024**2)}M"
    return f"{limit // 1024}K"


async def fetch_remote_metadata(url: str) -> VideoContext:
    args = [
        *ytdlp_command(),
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


async def download_remote_video(url: str, destination: Path, *, platform: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(destination.with_suffix(".%(ext)s"))
    max_duration = settings.max_video_duration_sec
    height = settings.remote_max_height

    args = [
        *ytdlp_command(),
        "--no-playlist",
        "--no-warnings",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--max-filesize",
        max_filesize_arg(),
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

    logger.info("remote_download_start", platform=platform, url=url)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoValidationError(
            f"Скачивание с {platform} заняло слишком много времени. Попробуйте позже."
        )
    output = (stderr or stdout or b"").decode("utf-8", errors="replace")

    if proc.returncode != 0:
        logger.warning("remote_download_failed", platform=platform, url=url, output=output[-2000:])
        raise VideoValidationError(map_ytdlp_error(output, platform=platform))

    if destination.exists():
        return destination

    for candidate in destination.parent.glob(f"{destination.stem}.*"):
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
            candidate.rename(destination)
            return destination

    raise VideoValidationError(f"Не удалось скачать видео с {platform}.")


def map_ytdlp_error(output: str, *, platform: str) -> str:
    lower = output.lower()
    if "private" in lower or "sign in" in lower or "login" in lower:
        return "Видео недоступно (приватное или требует авторизации)."
    if "geo" in lower or "not available" in lower:
        return "Видео недоступно в вашем регионе или удалено."
    if "filesize" in lower or "too large" in lower:
        limit_mb = settings.max_remote_download_bytes / (1024 * 1024)
        return f"Видео слишком большое (лимит {limit_mb:.0f} МБ)."
    if "duration" in lower:
        return f"Видео длиннее {settings.max_video_duration_sec // 60} минут."
    if "format" in lower or "filter specification" in lower:
        return f"Не удалось выбрать формат видео на {platform}. Попробуйте позже."
    return f"Не удалось скачать видео с {platform}. Проверьте ссылку и доступность ролика."
