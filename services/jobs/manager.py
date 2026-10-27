from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog
from aiogram import Bot

from config.settings import settings
from services.jobs.pipeline import run_analysis_pipeline, run_render_pipeline
from services.jobs.queue import JOB_QUEUE, PositionCallback
from services.storage.database import Database
from services.video.cleanup import cleanup_job_dir
from services.video.remote import RemoteVideoLink
from services.video.validation import VideoValidationError

logger = structlog.get_logger(__name__)

_active_tasks: dict[str, asyncio.Task] = {}


def create_job_dir() -> tuple[str, Path]:
    job_id = uuid.uuid4().hex
    job_dir = settings.temp_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


INTERRUPTED_TEXT = (
    "⚠️ Сервер бота перезапустился, и обработка этого видео прервалась.\n\n"
    "Отправьте видео или ссылку ещё раз."
)


async def clear_stale_locks() -> list[dict]:
    """
    После рестарта контейнера задачи в памяти пропали — снимаем зависшие блокировки.
    Возвращает прерванные задачи, чтобы предупредить их владельцев.
    """
    db = Database()
    jobs = await db.take_locked_jobs()
    if jobs:
        logger.warning("stale_locks_cleared", count=len(jobs))
    for job in jobs:
        if job.get("job_dir"):
            cleanup_job_dir(Path(job["job_dir"]))
    return jobs


async def notify_interrupted_jobs(bot: Bot, jobs: list[dict]) -> None:
    """Заменяет застывший прогресс прерванной задачи на понятное сообщение."""
    for job in jobs:
        chat_id = job["chat_id"]
        try:
            if job.get("progress_message_id"):
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=job["progress_message_id"],
                    text=INTERRUPTED_TEXT,
                    reply_markup=None,
                )
            else:
                await bot.send_message(chat_id, INTERRUPTED_TEXT)
        except Exception as exc:
            logger.warning("interrupted_notify_edit_failed", job_id=job["job_id"], error=str(exc))
            try:
                await bot.send_message(chat_id, INTERRUPTED_TEXT)
            except Exception as send_exc:
                logger.warning("interrupted_notify_failed", job_id=job["job_id"], error=str(send_exc))
                continue
        logger.info("interrupted_job_notified", job_id=job["job_id"], status=job.get("status"))


async def cancel_user_jobs(user_id: int) -> int:
    """Отменяет все задания пользователя (идущие и ждущие в очереди). Возвращает их число."""
    db = Database()
    job_ids = await db.get_active_job_ids(user_id)

    for job_id in job_ids:
        task = _active_tasks.get(job_id)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info("job_cancelled", job_id=job_id, user_id=user_id)
            except Exception as exc:
                logger.warning("job_cancel_wait_failed", job_id=job_id, error=str(exc))
        await db.unlock_job(job_id)
        job = await db.get_job(job_id)
        if job and job.get("status") == "rendering":
            await db.update_job(job_id, status="waiting_choice", progress=70)

    return len(job_ids)


async def check_user_slots(user_id: int) -> str | None:
    """Текст отказа, если пользователь уже отправил максимум видео; None — можно."""
    db = Database()
    active = await db.count_active_jobs(user_id)
    if active >= settings.max_jobs_per_user:
        return (
            f"⏳ У вас уже в работе {active} видео — это максимум. "
            "Дождитесь, пока одно закончится, или отправьте /cancel."
        )
    return None


def _queue_notifier(bot: Bot, chat_id: int, message_id: int) -> PositionCallback:
    async def notify(position: int) -> None:
        ahead = position + JOB_QUEUE.running - 1
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"⏳ Видео в очереди: перед ним {ahead} в обработке или ожидании.\n"
                "Начну автоматически, как только освободится место — ничего нажимать не нужно."
            ),
        )

    return notify


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
    refusal = await check_user_slots(user_id)
    if refusal:
        raise RuntimeError(refusal)

    job_id, job_dir = create_job_dir()
    await db.lock_job(user_id, job_id)
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
    db = Database()
    if job_id in _active_tasks or await db.is_job_active(job_id):
        raise RuntimeError("Это видео уже нарезается.")
    refusal = await check_user_slots(user_id)
    if refusal:
        raise RuntimeError(refusal)
    await db.lock_job(user_id, job_id)

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
    job = await db.get_job(job_id)
    on_wait = _queue_notifier(bot, chat_id, job["progress_message_id"]) if job else None
    try:
        async with JOB_QUEUE.slot(job_id, on_wait):
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
        try:
            job = await db.get_job(job_id)
            if job and job.get("progress_message_id"):
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=job["progress_message_id"],
                    text="⏹ Обработка прервана. Отправьте видео или ссылку ещё раз.",
                )
        except Exception:
            pass
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
        await db.unlock_job(job_id)

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
        async with JOB_QUEUE.slot(job_id, _queue_notifier(bot, chat_id, progress_message_id)):
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
        await db.unlock_job(job_id)
