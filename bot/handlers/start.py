from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states.processing import ProcessingStates

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.set_state(ProcessingStates.waiting_video)
    await message.answer(
        "👋 Привет! Я нарезаю короткие вертикальные хайлайты (9:16) из длинных видео.\n\n"
        "📹 Отправьте видеофайл (до 20 МБ) или ссылку Rutube / VK (до 20 мин).\n"
        "🔗 Rutube: https://rutube.ru/video/...\n"
        "🔗 VK: https://vk.com/video-...\n"
        "⚡ Я найду яркие моменты и предложу, сколько клипов сделать.\n\n"
        "Лимит: 10 видео в день."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Как пользоваться:\n"
        "1. Отправьте видео файлом (mp4, mov, до 20 МБ)\n"
        "   или ссылку Rutube / VK\n"
        "2. Дождитесь анализа (прогресс в процентах).\n"
        "3. Выберите количество клипов.\n"
        "4. Получите готовые вертикальные ролики.\n\n"
        "Команды: /start, /help, /status, /cancel"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    from services.jobs.manager import cancel_user_job

    if not message.from_user:
        return

    was_locked, job_id = await cancel_user_job(message.from_user.id)
    await state.set_state(ProcessingStates.waiting_video)
    if was_locked:
        await message.answer(
            "✅ Обработка остановлена, блокировка снята.\n\n"
            "Если анализ уже завершён — снова нажмите кнопку с числом клипов "
            "под сообщением с результатами.\n"
            "Или отправьте новое видео / ссылку Rutube."
        )
    else:
        await message.answer("Активной обработки нет. Отправьте видео или ссылку Rutube.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    from services.storage.database import Database

    db = Database()
    used = await db.get_daily_usage(message.from_user.id)
    active = await db.has_active_job(message.from_user.id)
    status = "идёт обработка" if active else "свободен"
    await message.answer(
        f"Сегодня обработано: {used}/10\n"
        f"Статус: {status}"
    )
