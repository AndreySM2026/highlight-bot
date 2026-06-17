from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_subtitles_keyboard(job_id: str, clip_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Да, с субтитрами",
                    callback_data=f"subs:{job_id}:{clip_count}:1",
                ),
                InlineKeyboardButton(
                    text="Без субтитров",
                    callback_data=f"subs:{job_id}:{clip_count}:0",
                ),
            ]
        ]
    )
