from __future__ import annotations

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


def _log_env() -> None:
    checks = {
        "BOT_TOKEN": bool(settings.bot_token),
        "TIMEWEB_API_TOKEN": bool(settings.timeweb_api_token),
        "TIMEWEB_AGENT_ID": bool(settings.timeweb_agent_id),
        "WEBHOOK_URL": bool(settings.webhook_url),
        "WEBHOOK_SECRET": bool(
            settings.webhook_secret
            and settings.webhook_secret not in ("change_me", "change_me_to_random_32_chars")
        ),
    }
    print("=== Env check ===", flush=True)
    for key, ok in checks.items():
        print(f"  {key}: {'OK' if ok else 'MISSING'}", flush=True)


async def init_bot(app: web.Application) -> None:
    _log_env()

    missing = []
    if not settings.bot_token:
        missing.append("BOT_TOKEN")
    if not settings.timeweb_api_token:
        missing.append("TIMEWEB_API_TOKEN")
    if not settings.timeweb_agent_id:
        missing.append("TIMEWEB_AGENT_ID")

    if missing:
        print(f"WARNING: missing env vars: {', '.join(missing)}", flush=True)
        print("Bot webhook disabled until vars are set.", flush=True)
        return

    from services.video.ffmpeg_check import ensure_ffmpeg_available

    ensure_ffmpeg_available()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    app["bot"] = bot
    app["dp"] = dp

    me = await bot.get_me()
    print(f"Bot authenticated: @{me.username}", flush=True)

    if settings.webhook_url:
        await bot.set_webhook(
            url=settings.full_webhook_url,
            drop_pending_updates=True,
        )
        print(f"Webhook set: {settings.full_webhook_url}", flush=True)
    else:
        print("WEBHOOK_URL not set — add it in Timeweb env vars", flush=True)

    logger.info("bot_initialized", username=me.username)
