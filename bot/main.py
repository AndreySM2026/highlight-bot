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

logger = structlog.get_logger(__name__)


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


async def root_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "highlight-bot"})


async def init_bot_background(app: web.Application) -> None:
    """Инициализация бота в фоне — /health отвечает сразу, без ожидания."""
    bot: Bot = app["bot"]
    try:
        from services.video.ffmpeg_check import ensure_ffmpeg_available

        ensure_ffmpeg_available()
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        settings.temp_dir.mkdir(parents=True, exist_ok=True)

        me = await bot.get_me()
        logger.info("bot_ready", username=me.username, id=me.id)

        if settings.webhook_url:
            await bot.set_webhook(
                url=settings.full_webhook_url,
                drop_pending_updates=True,
            )
            print(f"Бот запущен: @{me.username} | webhook: {settings.full_webhook_url}")
            logger.info("webhook_set", url=settings.full_webhook_url)
        else:
            print(f"Бот запущен: @{me.username} | WEBHOOK_URL не задан — добавьте и перезапустите")
            logger.warning("webhook_url_missing")
    except Exception as exc:
        logger.exception("bot_init_failed", error=str(exc))
        print(f"Ошибка инициализации бота: {exc}")


async def on_shutdown(app: web.Application) -> None:
    bot: Bot = app.get("bot")
    if bot:
        await bot.session.close()


def create_app() -> web.Application:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())

    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_requests_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(init_bot_background)
    app.on_shutdown.append(on_shutdown)
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


def _log_env_status() -> None:
    """Печатает в лог, какие переменные заданы (без значений)."""
    checks = {
        "BOT_TOKEN": bool(settings.bot_token),
        "TIMEWEB_API_TOKEN": bool(settings.timeweb_api_token),
        "TIMEWEB_AGENT_ID": bool(settings.timeweb_agent_id),
        "WEBHOOK_URL": bool(settings.webhook_url),
        "WEBHOOK_SECRET": bool(settings.webhook_secret and settings.webhook_secret != "change_me"),
        "HOST": settings.host,
        "PORT": str(settings.port),
    }
    print("=== Проверка переменных окружения ===")
    for key, value in checks.items():
        if isinstance(value, bool):
            status = "OK" if value else "НЕ ЗАДАНА"
            print(f"  {key}: {status}")
        else:
            print(f"  {key}: {value}")
    print("====================================")


def _validate_settings() -> None:
    missing = []
    if not settings.bot_token:
        missing.append("BOT_TOKEN")
    if not settings.timeweb_api_token:
        missing.append("TIMEWEB_API_TOKEN")
    if not settings.timeweb_agent_id:
        missing.append("TIMEWEB_AGENT_ID")
    if settings.webhook_url:
        if not settings.webhook_secret or settings.webhook_secret in ("change_me", "change_me_to_random_32_chars"):
            missing.append("WEBHOOK_SECRET")
        if "your-app" in settings.webhook_url:
            missing.append("WEBHOOK_URL (укажите реальный URL)")
    if missing:
        print(f"ОШИБКА: не заданы переменные: {', '.join(missing)}")
        print("Добавьте их в Timeweb → App Platform → highlights → Настройки → Переменные")
        raise SystemExit(1)


def _use_polling() -> bool:
    return os.getenv("USE_POLLING", "").lower() in ("1", "true", "yes")


def main() -> None:
    configure_logging()
    print("Highlight Bot — запуск...")
    _log_env_status()
    _validate_settings()

    if _use_polling():
        asyncio.run(run_polling())
        return

    print(f"Запуск HTTP-сервера на {settings.host}:{settings.port}")
    app = create_app()
    web.run_app(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
