from __future__ import annotations

from pathlib import Path

from aiogram import Bot


async def download_telegram_file(bot: Bot, file_id: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    if not file.file_path:
        raise RuntimeError("Telegram file path is empty")
    await bot.download_file(file.file_path, destination=destination)
    return destination
