from __future__ import annotations

from services.storage.database import Database
from config.settings import settings


async def check_daily_quota(user_id: int) -> str | None:
    db = Database()
    used = await db.get_daily_usage(user_id)
    if used >= settings.max_videos_per_day:
        return (
            f"⚠️ Лимит на сегодня исчерпан ({settings.max_videos_per_day} видео).\n"
            "Попробуйте завтра."
        )
    if await db.has_active_job(user_id):
        return "⏳ У вас уже идёт обработка. Дождитесь завершения."
    return None
