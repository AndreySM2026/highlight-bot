from __future__ import annotations

from aiogram import Router

from bot.handlers.clip_selection import router as clip_selection_router
from bot.handlers.video_link import router as video_link_router
from bot.handlers.start import router as start_router
from bot.handlers.video_upload import router as video_upload_router


def setup_routers() -> Router:
    root = Router()
    root.include_router(start_router)
    root.include_router(video_upload_router)
    root.include_router(video_link_router)
    root.include_router(clip_selection_router)
    return root
