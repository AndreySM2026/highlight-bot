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
    from services.jobs.manager import check_user_slots

    return await check_user_slots(user_id)
