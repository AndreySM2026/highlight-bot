from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states.processing import ProcessingStates
from config.settings import settings
from services.jobs.manager import start_analysis_job
from services.jobs.progress import progress_message
from services.jobs.quota import check_daily_quota
from services.storage.database import Database
from services.video.rutube import extract_rutube_url

router = Router()


@router.message(ProcessingStates.waiting_video, F.text)
async def handle_rutube_link(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return

    url = extract_rutube_url(message.text)
    if not url:
        await message.answer(
            "Отправьте видеофайл (до 20 МБ) или ссылку Rutube:\n"
            "https://rutube.ru/video/..."
        )
        return

    if not settings.rutube_enabled:
        await message.answer("Загрузка по ссылке Rutube временно отключена.")
        return

    quota_error = await check_daily_quota(message.from_user.id)
    if quota_error:
        await message.answer(quota_error)
        return

    progress_msg = await message.answer(
        progress_message(0, "Скачивание с Rutube (может занять несколько минут)")
    )

    db = Database()
    try:
        await start_analysis_job(
            message.bot,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            rutube_url=url,
            progress_message_id=progress_msg.message_id,
        )
        await db.increment_daily_usage(message.from_user.id)
        await state.set_state(ProcessingStates.analyzing)
    except Exception as exc:
        await progress_msg.edit_text(f"❌ Не удалось начать обработку: {exc}")
        await state.set_state(ProcessingStates.waiting_video)


@router.message(ProcessingStates.waiting_video, ~(F.video | F.document | F.text))
async def handle_unsupported(message: Message) -> None:
    await message.answer(
        "Отправьте видеофайл (mp4, mov, до 20 МБ) или ссылку Rutube."
    )
