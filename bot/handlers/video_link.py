from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states.processing import ProcessingStates
from services.jobs.manager import start_analysis_job
from services.jobs.progress import progress_message
from services.jobs.quota import check_daily_quota
from services.storage.database import Database
from services.video.remote import disabled_platform_hint, parse_video_link

router = Router()


@router.message(F.text)
async def handle_video_link(message: Message, state: FSMContext) -> None:
    """Принимает ссылку Rutube или VK (без обязательного /start)."""
    if not message.from_user or not message.text:
        return

    text = message.text.strip()
    if text.startswith("/"):
        return

    disabled = disabled_platform_hint(text)
    if disabled:
        await message.answer(disabled)
        return

    link = parse_video_link(text)
    if not link:
        current = await state.get_state()
        if current == ProcessingStates.waiting_video.state:
            await message.answer(
                "Отправьте видеофайл (до 20 МБ) или ссылку:\n"
                "• Rutube: https://rutube.ru/video/...\n"
                "• VK: https://vk.com/video-..."
            )
        return

    print(f"{link.label} link from user {message.from_user.id}: {link.url}", flush=True)

    quota_error = await check_daily_quota(message.from_user.id)
    if quota_error:
        await message.answer(quota_error)
        return

    db = Database()
    if await db.has_active_job(message.from_user.id):
        await message.answer(
            "У вас уже идёт обработка видео.\n"
            "Подождите завершения или отправьте /cancel для отмены."
        )
        return

    await message.answer(f"✅ Принял ссылку {link.label}.\nНачинаю скачивание…")

    progress_msg = await message.answer(
        progress_message(0, f"Скачивание с {link.label} (может занять несколько минут)")
    )

    try:
        await start_analysis_job(
            message.bot,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            video_link=link,
            progress_message_id=progress_msg.message_id,
        )
        await db.increment_daily_usage(message.from_user.id)
        await state.set_state(ProcessingStates.analyzing)
    except Exception as exc:
        await progress_msg.edit_text(f"❌ Не удалось начать обработку: {exc}")
        await state.set_state(ProcessingStates.waiting_video)
