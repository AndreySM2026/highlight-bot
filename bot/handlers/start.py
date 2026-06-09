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
        "📹 Отправьте видеофайл (до 20 минут).\n"
        "⚡ Я найду яркие моменты и предложу, сколько клипов сделать.\n\n"
        "Лимит: 10 видео в день."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Как пользоваться:\n"
        "1. Отправьте видео файлом (mp4, mov).\n"
        "2. Дождитесь анализа (прогресс в процентах).\n"
        "3. Выберите количество клипов.\n"
        "4. Получите готовые вертикальные ролики.\n\n"
        "Команды: /start, /help, /status"
    )


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
