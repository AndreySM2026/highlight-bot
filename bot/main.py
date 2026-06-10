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


def _flush_log(msg: str) -> None:
    print(msg, flush=True)


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def root_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "highlight-bot"})


async def _init_bot_background(app: web.Application) -> None:
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
            _flush_log(f"Бот запущен: @{me.username} | webhook: {settings.full_webhook_url}")
        else:
            _flush_log(f"Бот запущен: @{me.username} | WEBHOOK_URL не задан")
    except Exception as exc:
        logger.exception("bot_init_failed", error=str(exc))
        _flush_log(f"Ошибка инициализации бота: {exc}")


async def schedule_bot_init(app: web.Application) -> None:
    asyncio.create_task(_init_bot_background(app))


async def on_shutdown(app: web.Application) -> None:
    bot: Bot = app.get("bot")
    if bot:
        await bot.session.close()


def create_minimal_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    return app


def create_app() -> web.Application:
    from bot.handlers import setup_routers

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

    app.on_startup.append(schedule_bot_init)
    app.on_shutdown.append(on_shutdown)
    return app


async def run_polling() -> None:
    from bot.handlers import setup_routers
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
        await bot.delete_webhook(drop_pending_updates=False)

    _flush_log(f"Бот запущен: @{me.username} (polling)")
    await dp.start_polling(bot)


def _log_env_status() -> None:
    checks = {
        "BOT_TOKEN": bool(settings.bot_token),
        "TIMEWEB_API_TOKEN": bool(settings.timeweb_api_token),
        "TIMEWEB_AGENT_ID": bool(settings.timeweb_agent_id),
        "WEBHOOK_URL": bool(settings.webhook_url),
        "WEBHOOK_SECRET": bool(
            settings.webhook_secret
            and settings.webhook_secret not in ("change_me", "change_me_to_random_32_chars")
        ),
        "HOST": settings.host,
        "PORT": str(settings.port),
    }
    _flush_log("=== Проверка переменных окружения ===")
    for key, value in checks.items():
        if isinstance(value, bool):
            _flush_log(f"  {key}: {'OK' if value else 'НЕ ЗАДАНА'}")
        else:
            _flush_log(f"  {key}: {value}")
    _flush_log("====================================")


def _get_missing_settings() -> list[str]:
    missing = []
    if not settings.bot_token:
        missing.append("BOT_TOKEN")
    if not settings.timeweb_api_token:
        missing.append("TIMEWEB_API_TOKEN")
    if not settings.timeweb_agent_id:
        missing.append("TIMEWEB_AGENT_ID")
    if settings.webhook_url:
        if not settings.webhook_secret or settings.webhook_secret in (
            "change_me",
            "change_me_to_random_32_chars",
        ):
            missing.append("WEBHOOK_SECRET")
    return missing


def _use_polling() -> bool:
    return os.getenv("USE_POLLING", "").lower() in ("1", "true", "yes")


async def _run_web_server(app: web.Application) -> None:
    host = settings.host
    port = settings.port
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    _flush_log(f"HTTP-сервер слушает {host}:{port}")
    stop = asyncio.Event()
    await stop.wait()


def main() -> None:
    configure_logging()
    _flush_log("Highlight Bot — запуск...")
    _log_env_status()

    missing = _get_missing_settings()
    if missing:
        _flush_log(f"ВНИМАНИЕ: не заданы: {', '.join(missing)}")

    if _use_polling():
        if missing:
            raise SystemExit(1)
        asyncio.run(run_polling())
        return

    app = create_app() if not missing else create_minimal_app()
    _flush_log(f"Запуск HTTP-сервера на {settings.host}:{settings.port}")
    asyncio.run(_run_web_server(app))


if __name__ == "__main__":
    main()
