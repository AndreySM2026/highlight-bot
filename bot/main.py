from __future__ import annotations

import asyncio
import logging
import os
import sys

import structlog
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.handlers import setup_routers
from config.settings import settings


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def on_startup(bot: Bot) -> None:
    from services.video.ffmpeg_check import ensure_ffmpeg_available

    ensure_ffmpeg_available()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    me = await bot.get_me()

    if settings.webhook_url:
        await bot.set_webhook(
            url=settings.full_webhook_url,
            drop_pending_updates=True,
        )
        print(f"Бот запущен: @{me.username} | webhook: {settings.full_webhook_url}")
        structlog.get_logger(__name__).info(
            "webhook_set",
            url=settings.full_webhook_url,
            bot=me.username,
        )
    else:
        structlog.get_logger(__name__).warning(
            "webhook_url_missing",
            hint="Set WEBHOOK_URL for production. Use polling only for local dev.",
        )


async def on_shutdown(bot: Bot) -> None:
    await bot.delete_webhook(drop_pending_updates=False)


def create_app() -> web.Application:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/health", health_handler)

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_requests_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)
    return app


async def run_polling() -> None:
    """Локальная разработка без webhook."""
    from services.video.ffmpeg_check import ensure_ffmpeg_available

    ensure_ffmpeg_available()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    me = await bot.get_me()
    webhook = await bot.get_webhook_info()
    if webhook.url:
        print(f"Сбрасываю webhook ({webhook.url}) — переключаюсь на polling")
        await bot.delete_webhook(drop_pending_updates=False)

    print(f"Бот запущен: @{me.username} (polling). Ожидаю сообщения...")
    print("Не закрывайте этот терминал. Для остановки: Ctrl+C")

    await dp.start_polling(bot)


def _validate_settings() -> None:
    missing = []
    if not settings.bot_token:
        missing.append("BOT_TOKEN")
    if not settings.timeweb_api_token:
        missing.append("TIMEWEB_API_TOKEN")
    if not settings.timeweb_agent_id:
        missing.append("TIMEWEB_AGENT_ID")
    if settings.webhook_url:
        if not settings.webhook_secret or settings.webhook_secret == "change_me":
            missing.append("WEBHOOK_SECRET (задайте случайную строку)")
        if "your-app" in settings.webhook_url:
            missing.append("WEBHOOK_URL (укажите реальный URL приложения)")
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


def _use_polling() -> bool:
    """Polling только для локальной разработки."""
    return os.getenv("USE_POLLING", "").lower() in ("1", "true", "yes")


def main() -> None:
    configure_logging()
    _validate_settings()

    if _use_polling():
        asyncio.run(run_polling())
        return

    # На Timeweb/Docker всегда HTTP-сервер (порт 8080), даже до настройки WEBHOOK_URL.
    app = create_app()
    web.run_app(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
