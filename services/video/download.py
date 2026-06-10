from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from services.video.validation import VideoValidationError, file_too_large_message
from config.settings import settings


async def download_telegram_file(bot: Bot, file_id: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        file = await bot.get_file(file_id)
    except TelegramBadRequest as exc:
        if "too big" in str(exc).lower():
            raise VideoValidationError(
                file_too_large_message(settings.max_upload_bytes, settings.max_upload_bytes)
            ) from exc
        raise
    if not file.file_path:
        raise RuntimeError("Telegram file path is empty")
    try:
        await bot.download_file(file.file_path, destination=destination)
    except TelegramBadRequest as exc:
        if "too big" in str(exc).lower():
            raise VideoValidationError(
                file_too_large_message(settings.max_upload_bytes, settings.max_upload_bytes)
            ) from exc
        raise
    return destination
