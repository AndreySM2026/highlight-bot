from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_clip_count_keyboard(job_id: str, total_found: int, recommended: int) -> InlineKeyboardMarkup:
    options = sorted({1, 3, recommended, total_found, min(5, total_found), min(7, total_found)})
    options = [n for n in options if 1 <= n <= total_found]
    options = sorted(set(options))

    buttons: list[InlineKeyboardButton] = []
    for count in options:
        label = f"{count}"
        if count == recommended:
            label = f"⭐ {count}"
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"clips:{job_id}:{count}",
            )
        )

    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    if total_found not in options:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Все ({total_found})",
                    callback_data=f"clips:{job_id}:{total_found}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
