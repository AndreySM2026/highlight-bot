from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.states.processing import ProcessingStates
from services.jobs.manager import start_render_job
from services.storage.database import Database

router = Router()


@router.callback_query(F.data.startswith("clips:"))
async def handle_clip_selection(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    _, job_id, count_raw = parts
    try:
        clip_count = int(count_raw)
    except ValueError:
        await callback.answer("Некорректное число.", show_alert=True)
        return

    db = Database()
    job = await db.get_job(job_id)
    if not job:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    if job["user_id"] != callback.from_user.id:
        await callback.answer("Это не ваша задача.", show_alert=True)
        return

    if job["status"] != "waiting_choice":
        await callback.answer("Задача уже обрабатывается.", show_alert=True)
        return

    # После анализа lock должен быть снят; если остался (старый баг) — снимаем.
    if await db.has_active_job(callback.from_user.id):
        await db.unlock_user(callback.from_user.id)

    await callback.answer(f"Рендерим {clip_count} клипов...")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await start_render_job(
            callback.bot,
            job_id=job_id,
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            clip_count=clip_count,
            progress_message_id=callback.message.message_id,
        )
        await state.set_state(ProcessingStates.rendering)
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось начать рендер: {exc}")
