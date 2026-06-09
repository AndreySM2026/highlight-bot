from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog
from aiogram import Bot

from config.settings import settings
from services.jobs.pipeline import run_analysis_pipeline, run_render_pipeline
from services.storage.database import Database

logger = structlog.get_logger(__name__)

_active_tasks: dict[str, asyncio.Task] = {}


def create_job_dir() -> tuple[str, Path]:
    job_id = uuid.uuid4().hex
    job_dir = settings.temp_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


async def start_analysis_job(
    bot: Bot,
    *,
    user_id: int,
    chat_id: int,
    file_id: str,
    mime_type: str | None,
    progress_message_id: int,
) -> str:
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
        _run_analysis_wrapper(bot, job_id, user_id, chat_id, file_id, mime_type, job_dir)
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
) -> None:
    if job_id in _active_tasks:
        raise RuntimeError("Задача уже выполняется.")

    db = Database()
    if await db.has_active_job(user_id):
        raise RuntimeError("У вас уже идёт обработка.")
    await db.lock_user(user_id, job_id)

    task = asyncio.create_task(
        _run_render_wrapper(bot, job_id, user_id, chat_id, clip_count, progress_message_id)
    )
    _active_tasks[job_id] = task
    task.add_done_callback(lambda _: _active_tasks.pop(job_id, None))


async def _run_analysis_wrapper(
    bot: Bot,
    job_id: str,
    user_id: int,
    chat_id: int,
    file_id: str,
    mime_type: str | None,
    job_dir: Path,
) -> None:
    db = Database()
    try:
        await run_analysis_pipeline(
            bot,
            job_id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            file_id=file_id,
            mime_type=mime_type,
            job_dir=job_dir,
        )
    except Exception as exc:
        logger.exception("analysis_job_failed", job_id=job_id, error=str(exc))
        job = await db.get_job(job_id)
        error_text = f"❌ Ошибка обработки: {exc}"
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
    finally:
        await db.unlock_user(user_id)


async def _run_render_wrapper(
    bot: Bot,
    job_id: str,
    user_id: int,
    chat_id: int,
    clip_count: int,
    progress_message_id: int,
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
        )
    except Exception as exc:
        logger.exception("render_job_failed", job_id=job_id, error=str(exc))
        try:
            await bot.send_message(chat_id, f"❌ Ошибка рендера: {exc}")
        except Exception:
            pass
    finally:
        await db.unlock_user(user_id)
