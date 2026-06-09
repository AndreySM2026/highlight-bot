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
from services.storage.database import Database

router = Router()


def _extract_video(message: Message) -> tuple[str, str | None] | None:
    if message.video:
        return message.video.file_id, message.video.mime_type
    if message.document:
        mime = message.document.mime_type
        name = (message.document.file_name or "").lower()
        suffix = Path(name).suffix
        if mime and mime.startswith("video/"):
            return message.document.file_id, mime
        if suffix in ALLOWED_VIDEO_EXTENSIONS:
            return message.document.file_id, mime or "video/mp4"
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
    used = await db.get_daily_usage(message.from_user.id)
    if used >= settings.max_videos_per_day:
        await message.answer(
            f"⚠️ Лимит на сегодня исчерпан ({settings.max_videos_per_day} видео).\n"
            "Попробуйте завтра."
        )
        return

    if await db.has_active_job(message.from_user.id):
        await message.answer("⏳ У вас уже идёт обработка. Дождитесь завершения.")
        return

    file_id, mime_type = video_info
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


@router.message(ProcessingStates.waiting_video)
async def handle_non_video(message: Message) -> None:
    await message.answer("Отправьте видеофайл. Текстовые сообщения пока не обрабатываются.")
