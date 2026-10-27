from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config.settings import settings
from bot.states.processing import ProcessingStates

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.set_state(ProcessingStates.waiting_video)
    await message.answer(
        "👋 Привет! Я нарезаю короткие вертикальные хайлайты (9:16) из длинных видео.\n\n"
        f"📹 Отправьте видеофайл (до 20 МБ) или ссылку Rutube / VK (до {settings.max_video_duration_sec // 60} мин).\n"
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
    from services.jobs.manager import cancel_user_jobs

    if not message.from_user:
        return

    cancelled = await cancel_user_jobs(message.from_user.id)
    await state.set_state(ProcessingStates.waiting_video)
    if cancelled:
        await message.answer(
            f"✅ Остановлено видео: {cancelled}.\n\n"
            "Если анализ уже завершён — снова нажмите кнопку с числом клипов "
            "под сообщением с результатами.\n"
            "Или отправьте новое видео / ссылку."
        )
    else:
        await message.answer("Активной обработки нет. Отправьте видео или ссылку.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    from services.jobs.queue import JOB_QUEUE
    from services.storage.database import Database

    db = Database()
    used = await db.get_daily_usage(message.from_user.id)
    active = await db.count_active_jobs(message.from_user.id)
    status = f"в работе ваших видео: {active} из {settings.max_jobs_per_user}" if active else "свободен"
    await message.answer(
        f"Сегодня обработано: {used}/{settings.max_videos_per_day}\n"
        f"Статус: {status}\n"
        f"Бот сейчас обрабатывает: {JOB_QUEUE.running} из {settings.max_parallel_jobs}, "
        f"в очереди: {JOB_QUEUE.waiting}"
    )
