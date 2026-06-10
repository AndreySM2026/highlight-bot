from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

logger = structlog.get_logger(__name__)


class UpdateLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            kind = "message" if event.message else "callback" if event.callback_query else "other"
            uid = event.update_id
            print(f"Telegram update #{uid} ({kind})", flush=True)
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("handler_error", update_id=getattr(event, "update_id", None))
            raise
