from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.keyboards.subtitles import build_subtitles_keyboard
from bot.states.processing import ProcessingStates
from config.settings import settings
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

    if job["status"] not in {"waiting_choice", "rendering"}:
        await callback.answer("Задача уже обработана или устарела.", show_alert=True)
        return

    if await db.has_active_job(callback.from_user.id):
        await callback.answer("Уже идёт обработка. Подождите или /cancel", show_alert=True)
        return

    await callback.answer(f"Выбрано клипов: {clip_count}")

    if settings.subtitles_enabled:
        transcript_hint = ""
        if (Path(job["job_dir"]) / "transcript.json").exists():
            transcript_hint = "\n\n📝 Текст для субтитров готов (Whisper)."
        await callback.message.edit_text(
            f"Клипов: *{clip_count}*\n\nДобавить русские субтитры?{transcript_hint}",
            parse_mode="Markdown",
            reply_markup=build_subtitles_keyboard(job_id, clip_count),
        )
        await state.set_state(ProcessingStates.waiting_subtitles)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    try:
        await start_render_job(
            callback.bot,
            job_id=job_id,
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            clip_count=clip_count,
            progress_message_id=callback.message.message_id,
            with_subtitles=False,
        )
        await state.set_state(ProcessingStates.rendering)
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось начать рендер: {exc}")


@router.callback_query(F.data.startswith("subs:"))
async def handle_subtitle_selection(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    _, job_id, count_raw, subs_raw = parts
    try:
        clip_count = int(count_raw)
        with_subtitles = subs_raw == "1"
    except ValueError:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    db = Database()
    job = await db.get_job(job_id)
    if not job:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    if job["user_id"] != callback.from_user.id:
        await callback.answer("Это не ваша задача.", show_alert=True)
        return

    if await db.has_active_job(callback.from_user.id):
        await callback.answer("Уже идёт обработка. Подождите или /cancel", show_alert=True)
        return

    label = "с субтитрами" if with_subtitles else "без субтитров"
    await callback.answer(f"Рендер {clip_count} клипов {label}")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await start_render_job(
            callback.bot,
            job_id=job_id,
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            clip_count=clip_count,
            progress_message_id=callback.message.message_id,
            with_subtitles=with_subtitles,
        )
        await state.set_state(ProcessingStates.rendering)
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось начать рендер: {exc}")
