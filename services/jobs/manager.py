from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog
from aiogram import Bot

from config.settings import settings
from services.jobs.pipeline import run_analysis_pipeline, run_render_pipeline
from services.storage.database import Database
from services.video.remote import RemoteVideoLink
from services.video.validation import VideoValidationError

logger = structlog.get_logger(__name__)

_active_tasks: dict[str, asyncio.Task] = {}


def create_job_dir() -> tuple[str, Path]:
    job_id = uuid.uuid4().hex
    job_dir = settings.temp_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


async def clear_stale_locks() -> int:
    """После рестарта контейнера задачи в памяти пропали — снимаем зависшие блокировки."""
    db = Database()
    count = await db.clear_all_locks()
    if count:
        logger.warning("stale_locks_cleared", count=count)
    return count


async def cancel_user_job(user_id: int) -> tuple[bool, str | None]:
    """
    Отменяет фоновую задачу и снимает блокировку.
    Возвращает (была_ли_блокировка, job_id).
    """
    db = Database()
    job_id = await db.get_locked_job_id(user_id)
    was_locked = job_id is not None

    if job_id and job_id in _active_tasks:
        task = _active_tasks[job_id]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info("job_cancelled", job_id=job_id, user_id=user_id)
        except Exception as exc:
            logger.warning("job_cancel_wait_failed", job_id=job_id, error=str(exc))

    await db.unlock_user(user_id)

    if job_id:
        job = await db.get_job(job_id)
        if job and job.get("status") in {"rendering", "downloading", "normalizing", "transcribing", "analyzing", "metadata"}:
            await db.update_job(job_id, status="waiting_choice", progress=70)

    return was_locked, job_id


async def start_analysis_job(
    bot: Bot,
    *,
    user_id: int,
    chat_id: int,
    progress_message_id: int,
    file_id: str | None = None,
    video_link: RemoteVideoLink | None = None,
    mime_type: str | None = None,
) -> str:
    if bool(file_id) == bool(video_link):
        raise ValueError("Укажите file_id или video_link")

    db = Database()
    if await db.has_active_job(user_id):
        raise RuntimeError("У вас уже идёт обработка другого видео.")

    job_id, job_dir = create_job_dir()
    await db.lock_user(user_id, job_id)
    await db.create_job(
        job_id,
        user_id,
        chat_id,
        str(job_dir),
        progress_message_id=progress_message_id,
    )

    task = asyncio.create_task(
        _run_analysis_wrapper(
            bot,
            job_id,
            user_id,
            chat_id,
            job_dir,
            file_id=file_id,
            video_link=video_link,
            mime_type=mime_type,
        )
    )
    _active_tasks[job_id] = task
    task.add_done_callback(lambda _: _active_tasks.pop(job_id, None))
    return job_id


async def start_render_job(
    bot: Bot,
    *,
    job_id: str,
    user_id: int,
    chat_id: int,
    clip_count: int,
    progress_message_id: int,
    with_subtitles: bool = False,
) -> None:
    if job_id in _active_tasks:
        raise RuntimeError("Задача уже выполняется.")

    db = Database()
    if await db.has_active_job(user_id):
        raise RuntimeError("У вас уже идёт обработка.")
    await db.lock_user(user_id, job_id)

    task = asyncio.create_task(
        _run_render_wrapper(
            bot, job_id, user_id, chat_id, clip_count, progress_message_id, with_subtitles
        )
    )
    _active_tasks[job_id] = task
    task.add_done_callback(lambda _: _active_tasks.pop(job_id, None))


async def _run_analysis_wrapper(
    bot: Bot,
    job_id: str,
    user_id: int,
    chat_id: int,
    job_dir: Path,
    *,
    file_id: str | None = None,
    video_link: RemoteVideoLink | None = None,
    mime_type: str | None = None,
) -> None:
    db = Database()
    error_text: str | None = None
    try:
        await run_analysis_pipeline(
            bot,
            job_id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            job_dir=job_dir,
            file_id=file_id,
            video_link=video_link,
            mime_type=mime_type,
        )
    except asyncio.CancelledError:
        logger.info("analysis_job_cancelled", job_id=job_id)
        raise
    except VideoValidationError as exc:
        logger.warning("analysis_job_validation_failed", job_id=job_id, error=str(exc))
        error_text = str(exc)
    except Exception as exc:
        logger.exception("analysis_job_failed", job_id=job_id, error=str(exc))
        error_text = f"❌ Ошибка обработки: {exc}"
    else:
        return
    finally:
        await db.unlock_user(user_id)

    if error_text:
        job = await db.get_job(job_id)
        try:
            if job and job.get("progress_message_id"):
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=job["progress_message_id"],
                    text=error_text,
                )
            else:
                await bot.send_message(chat_id, error_text)
        except Exception:
            pass


async def _run_render_wrapper(
    bot: Bot,
    job_id: str,
    user_id: int,
    chat_id: int,
    clip_count: int,
    progress_message_id: int,
    with_subtitles: bool,
) -> None:
    db = Database()
    try:
        await run_render_pipeline(
            bot,
            job_id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            clip_count=clip_count,
            progress_message_id=progress_message_id,
            with_subtitles=with_subtitles,
        )
    except asyncio.CancelledError:
        logger.info("render_job_cancelled", job_id=job_id)
        await db.update_job(job_id, status="waiting_choice", progress=70)
        try:
            await bot.send_message(
                chat_id,
                "⏹ Рендер остановлен. Нажмите кнопку с числом клипов ещё раз или отправьте новое видео.",
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.exception("render_job_failed", job_id=job_id, error=str(exc))
        await db.update_job(job_id, status="waiting_choice", progress=70)
        try:
            await bot.send_message(chat_id, f"❌ Ошибка рендера: {exc}")
        except Exception:
            pass
    finally:
        await db.unlock_user(user_id)
