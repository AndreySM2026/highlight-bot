from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from aiogram import Bot
from aiogram.types import FSInputFile

from config.settings import settings
from services.highlights.detector import detect_highlights
from services.highlights.schemas import HighlightResult, VideoContext
from services.jobs.progress import progress_message, stage_to_percent
from services.storage.database import Database
from services.video.activity_map import build_activity_map
from services.video.cleanup import cleanup_job_dir
from services.video.aspect import analyze_video_geometry
from services.video.clip import render_clip
from services.video.download import download_telegram_file
from services.video.normalize import normalize_video
from services.video.rutube import download_rutube_video, fetch_rutube_metadata
from services.video.validation import VideoValidationError, validate_video
from bot.keyboards.clip_count import build_clip_count_keyboard

logger = structlog.get_logger(__name__)


async def _run_stage_with_heartbeat(
    coro,
    bot: Bot,
    chat_id: int,
    message_id: int,
    job_id: str,
    stage: str,
    label: str,
    *,
    ratio_start: float = 0.2,
    ratio_end: float = 0.95,
) -> None:
    """Периодически обновляет прогресс, пока длится тяжёлый этап (ffmpeg)."""
    task = asyncio.create_task(coro)
    tick = 0
    while not task.done():
        _, pending = await asyncio.wait({task}, timeout=20)
        if not pending:
            tick += 1
            ratio = ratio_start + (ratio_end - ratio_start) * min(0.95, tick * 0.08)
            await _update_progress(
                bot,
                chat_id,
                message_id,
                job_id,
                stage,
                ratio,
                f"{label} (ещё работаем…)",
            )
    await task


async def _update_progress(
    bot: Bot,
    chat_id: int,
    message_id: int,
    job_id: str,
    stage: str,
    ratio: float = 1.0,
    text: str = "Обработка",
) -> None:
    percent = stage_to_percent(stage, ratio)
    db = Database()
    await db.update_job(job_id, progress=percent, status=stage)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=progress_message(percent, text),
        )
    except Exception:
        pass


async def run_analysis_pipeline(
    bot: Bot,
    *,
    job_id: str,
    user_id: int,
    chat_id: int,
    job_dir: Path,
    file_id: str | None = None,
    rutube_url: str | None = None,
    mime_type: str | None = None,
) -> None:
    if bool(file_id) == bool(rutube_url):
        raise ValueError("Укажите file_id или rutube_url")

    db = Database()
    job = await db.get_job(job_id)
    if not job:
        raise RuntimeError("Job not found")

    progress_message_id = job["progress_message_id"]

    video_context = VideoContext()
    download_label = "Скачивание с Rutube" if rutube_url else "Скачивание"
    await _update_progress(bot, chat_id, progress_message_id, job_id, "downloading", 0.2, download_label)
    input_path = job_dir / "input.mp4"
    if rutube_url:
        try:
            video_context = await fetch_rutube_metadata(rutube_url)
            (job_dir / "video_context.json").write_text(
                video_context.model_dump_json(ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("rutube_metadata_failed", error=str(exc))
        await download_rutube_video(rutube_url, input_path)
    else:
        await download_telegram_file(bot, file_id, input_path)

    await _update_progress(bot, chat_id, progress_message_id, job_id, "downloading", 1.0, "Скачивание")
    duration = await validate_video(input_path, mime_type)

    try:
        geometry = await analyze_video_geometry(input_path)
        (job_dir / "geometry.json").write_text(json.dumps(geometry), encoding="utf-8")
    except Exception as exc:
        logger.warning("geometry_analysis_failed", error=str(exc))

    await _update_progress(bot, chat_id, progress_message_id, job_id, "normalizing", 0.1, "Нормализация")
    normalized_path = job_dir / "normalized.mp4"
    await _run_stage_with_heartbeat(
        normalize_video(input_path, normalized_path),
        bot,
        chat_id,
        progress_message_id,
        job_id,
        "normalizing",
        "Нормализация",
    )

    await _update_progress(bot, chat_id, progress_message_id, job_id, "normalizing", 1.0, "Нормализация")

    await _update_progress(bot, chat_id, progress_message_id, job_id, "metadata", 0.4, "Анализ метаданных")
    activity_map = await build_activity_map(normalized_path, duration)
    await _update_progress(bot, chat_id, progress_message_id, job_id, "metadata", 1.0, "Анализ метаданных")

    await _update_progress(bot, chat_id, progress_message_id, job_id, "analyzing", 0.5, "Поиск хайлайтов")
    highlights: HighlightResult = await detect_highlights(activity_map, video_context)
    await _update_progress(bot, chat_id, progress_message_id, job_id, "analyzing", 1.0, "Поиск хайлайтов")

    if not highlights.segments:
        raise RuntimeError("Не удалось найти яркие моменты в видео.")

    await db.save_job_highlights(job_id, highlights.model_dump())

    total_found = len(highlights.segments)
    recommended = highlights.recommended_clip_count
    source_label = "Qwen 3.5" if highlights.source == "qwen" else "эвристика"
    theme_line = ""
    if highlights.video_theme:
        theme_line = f"📌 Тема: _{highlights.video_theme}_\n\n"

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=progress_message_id,
        text=(
            f"✅ Анализ завершён ({source_label}).\n\n"
            f"{theme_line}"
            f"Нашёл *{total_found}* законченных идей.\n"
            f"Рекомендую сделать *{recommended}* клипов.\n\n"
            f"Выберите количество:"
        ),
        parse_mode="Markdown",
        reply_markup=build_clip_count_keyboard(job_id, total_found, recommended),
    )
    await db.update_job(job_id, status="waiting_choice", progress=stage_to_percent("waiting_choice"))


async def run_render_pipeline(
    bot: Bot,
    *,
    job_id: str,
    user_id: int,
    chat_id: int,
    clip_count: int,
    progress_message_id: int,
) -> None:
    db = Database()
    job = await db.get_job(job_id)
    if not job:
        raise RuntimeError("Задача не найдена.")

    highlights_data = await db.get_job_highlights(job_id)
    if not highlights_data:
        raise RuntimeError("Результаты анализа не найдены.")

    highlights = HighlightResult.model_validate(highlights_data)
    segments = highlights.segments[:clip_count]
    job_dir = Path(job["job_dir"])
    normalized_path = job_dir / "normalized.mp4"
    source_path = job_dir / "input.mp4"

    if not normalized_path.exists():
        raise RuntimeError("Нормализованное видео не найдено.")
    if not source_path.exists():
        source_path = normalized_path

    geometry: dict | None = None
    geometry_file = job_dir / "geometry.json"
    if geometry_file.exists():
        try:
            geometry = json.loads(geometry_file.read_text(encoding="utf-8"))
        except Exception:
            geometry = None

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=progress_message_id,
        text=progress_message(stage_to_percent("rendering", 0), "Рендер клипов"),
        reply_markup=None,
    )

    rendered_paths: list[Path] = []
    total = len(segments)
    for idx, segment in enumerate(segments, start=1):
        ratio = idx / max(total, 1)
        await _update_progress(
            bot,
            chat_id,
            progress_message_id,
            job_id,
            "rendering",
            ratio * 0.9,
            f"Рендер клипа {idx}/{total}",
        )
        output_path = job_dir / f"clip_{idx:03d}.mp4"
        rendered = await render_clip(
            source_path,
            segment,
            output_path,
            geometry=geometry,
        )
        rendered_paths.append(rendered)

    await _update_progress(bot, chat_id, progress_message_id, job_id, "sending", 0.2, "Отправка")

    for idx, (clip_path, segment) in enumerate(zip(rendered_paths, segments), start=1):
        duration_sec = int(segment.end_time - segment.start_time)
        caption_parts = [f"Клип {idx}/{total} · {duration_sec} сек", segment.title]
        if segment.reason:
            caption_parts.append(segment.reason)
        caption = "\n".join(caption_parts)[:1024]
        await bot.send_video(
            chat_id=chat_id,
            video=FSInputFile(clip_path),
            caption=caption,
            supports_streaming=True,
        )

    await _update_progress(bot, chat_id, progress_message_id, job_id, "sending", 1.0, "Отправка")

    cleanup_job_dir(job_dir)
    await db.update_job(job_id, status="done", progress=100)

    await bot.send_message(
        chat_id,
        "🎬 Готово! Все клипы отправлены.\nОтправьте следующее видео (до 20 минут).",
    )
    # Сбрасываем inline-сообщение прогресса в финальный статус
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_message_id,
            text="✅ Обработка завершена на 100%",
        )
    except Exception:
        pass
