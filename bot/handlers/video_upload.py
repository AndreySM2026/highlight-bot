from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states.processing import ProcessingStates
from config.constants import ALLOWED_VIDEO_EXTENSIONS
from config.settings import settings
from services.jobs.manager import start_analysis_job
from services.jobs.progress import progress_message
from services.jobs.quota import check_daily_quota
from services.storage.database import Database
from services.video.validation import file_too_large_message


router = Router()


def _extract_video(message: Message) -> tuple[str, str | None, int | None] | None:
    if message.video:
        return message.video.file_id, message.video.mime_type, message.video.file_size
    if message.document:
        mime = message.document.mime_type
        name = (message.document.file_name or "").lower()
        suffix = Path(name).suffix
        if mime and mime.startswith("video/"):
            return message.document.file_id, mime, message.document.file_size
        if suffix in ALLOWED_VIDEO_EXTENSIONS:
            return message.document.file_id, mime or "video/mp4", message.document.file_size
    return None


@router.message(F.video | F.document)
async def handle_video_upload(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    video_info = _extract_video(message)
    if not video_info:
        await message.answer("Пожалуйста, отправьте видеофайл (mp4, mov).")
        return

    db = Database()
    quota_error = await check_daily_quota(message.from_user.id)
    if quota_error:
        await message.answer(quota_error)
        return

    file_id, mime_type, file_size = video_info

    if file_size and file_size > settings.max_upload_bytes:
        await message.answer(file_too_large_message(file_size, settings.max_upload_bytes))
        return

    progress_msg = await message.answer(progress_message(0, "Скачивание"))

    try:
        await start_analysis_job(
            message.bot,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            file_id=file_id,
            mime_type=mime_type,
            progress_message_id=progress_msg.message_id,
        )
        await db.increment_daily_usage(message.from_user.id)
        await state.set_state(ProcessingStates.analyzing)
    except Exception as exc:
        await progress_msg.edit_text(f"❌ Не удалось начать обработку: {exc}")
        await state.set_state(ProcessingStates.waiting_video)

