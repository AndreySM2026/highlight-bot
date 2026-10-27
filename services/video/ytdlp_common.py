from __future__ import annotations

import asyncio
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
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


ProgressCallback = Callable[[float, str], Awaitable[None]]

# Строка прогресса, которую печатает yt-dlp по нашему --progress-template
_PROGRESS_RE = re.compile(r"^HLPROG (\S+) (\S+) (\S+)$")


def _parse_progress(line: str) -> tuple[float, float] | None:
    """(скачано байт, всего байт) из строки прогресса; None — не строка прогресса."""
    match = _PROGRESS_RE.match(line.strip())
    if not match:
        return None
    downloaded, total, estimate = match.groups()
    try:
        done = float(downloaded)
    except ValueError:
        return None
    for raw in (total, estimate):
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 0:
            return done, value
    return None


async def _read_progress(stream: asyncio.StreamReader, on_progress: ProgressCallback | None) -> None:
    """
    Читает прогресс yt-dlp построчно. Видео и звук качаются отдельно (bv+ba):
    первый поток — 0–95% шкалы, второй (звук, он маленький) — оставшиеся 5%.
    """
    stream_index = 0
    last_fraction = 0.0
    last_sent = 0.0
    last_time = 0.0
    loop = asyncio.get_running_loop()
    while True:
        raw = await stream.readline()
        if not raw:
            break
        parsed = _parse_progress(raw.decode("utf-8", errors="replace"))
        if not parsed or on_progress is None:
            continue
        done, total = parsed
        fraction = min(1.0, done / total)
        if fraction + 0.2 < last_fraction:  # начался следующий поток
            stream_index += 1
        last_fraction = fraction
        overall = fraction * 0.95 if stream_index == 0 else 0.95 + fraction * 0.05
        now = loop.time()
        # Telegram ограничивает частоту правок — шлём не чаще раза в 4 с и при заметном сдвиге
        if overall - last_sent >= 0.02 and now - last_time >= 4:
            last_sent, last_time = overall, now
            mb_done, mb_total = done / 1024**2, total / 1024**2
            detail = f"{mb_done:.0f} из {mb_total:.0f} МБ" if stream_index == 0 else "звук"
            try:
                await on_progress(overall, detail)
            except Exception as exc:
                logger.debug("download_progress_callback_failed", error=str(exc))


async def download_remote_video(
    url: str,
    destination: Path,
    *,
    platform: str,
    on_progress: ProgressCallback | None = None,
) -> Path:
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
        "--newline",
        "--progress-template",
        "download:HLPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s",
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
    # stdout (прогресс) и stderr (ошибки) читаем одновременно, иначе переполненный канал повесит процесс
    progress_task = asyncio.create_task(_read_progress(proc.stdout, on_progress))
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        await asyncio.wait_for(proc.wait(), timeout=3600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        progress_task.cancel()
        stderr_task.cancel()
        raise VideoValidationError(
            f"Скачивание с {platform} заняло слишком много времени. Попробуйте позже."
        )
    await progress_task
    stderr = await stderr_task
    output = stderr.decode("utf-8", errors="replace")

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
