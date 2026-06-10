from __future__ import annotations

import asyncio
import socket

import structlog
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.handlers import setup_routers
from bot.middleware.logging import UpdateLoggingMiddleware
from config.settings import settings

logger = structlog.get_logger(__name__)

TELEGRAM_TIMEOUT_SEC = 30
WEBHOOK_RETRIES = 5
ACTIVATION_RETRY_SEC = 60


def _telegram_session() -> AiohttpSession:
    """IPv4-only; опционально proxy или свой Bot API сервер."""
    kwargs: dict = {"timeout": TELEGRAM_TIMEOUT_SEC}
    if settings.telegram_proxy:
        kwargs["proxy"] = settings.telegram_proxy
    if settings.telegram_api_base_url:
        base = settings.telegram_api_base_url.rstrip("/")
        kwargs["api"] = TelegramAPIServer.from_base(base)
        print(f"Telegram API: {base}", flush=True)
    elif settings.telegram_proxy:
        print(f"Telegram proxy: {settings.telegram_proxy.split('@')[-1]}", flush=True)
    session = AiohttpSession(**kwargs)
    session._connector_init["family"] = socket.AF_INET
    return session


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


def configure_app(app: web.Application) -> Bot | None:
    """
    Регистрация маршрутов webhook — ДО запуска HTTP-сервера.
    После старта aiohttp «замораживает» app и добавлять маршруты нельзя.
    """
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
        print("Webhook routes not registered.", flush=True)
        return None

    session = _telegram_session()
    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(UpdateLoggingMiddleware())
    dp.include_router(setup_routers())

    @dp.errors()
    async def on_error(event: object) -> None:
        logger.exception("dispatcher_error", event=repr(event))
        print(f"Dispatcher error: {event!r}", flush=True)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    app["bot"] = bot
    app["dp"] = dp
    print(f"Webhook route registered: {settings.webhook_path}", flush=True)
    return bot


async def _try_activate_once(bot: Bot) -> bool:
    """Одна попытка get_me + set_webhook. True — бот готов."""
    me = None
    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            me = await bot.get_me()
            print(f"Bot authenticated: @{me.username} (attempt {attempt})", flush=True)
            break
        except Exception as exc:
            print(f"get_me attempt {attempt}/{WEBHOOK_RETRIES} failed: {exc}", flush=True)
            if attempt < WEBHOOK_RETRIES:
                await asyncio.sleep(3 * attempt)
            else:
                return False

    if not settings.webhook_url:
        print("WEBHOOK_URL not set — add it in Timeweb env vars", flush=True)
        return bool(me)

    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            await bot.set_webhook(
                url=settings.full_webhook_url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=True,
            )
            print(f"Webhook set: {settings.full_webhook_url}", flush=True)
            logger.info("webhook_set", url=settings.full_webhook_url, username=me.username)
            return True
        except Exception as exc:
            print(f"set_webhook attempt {attempt}/{WEBHOOK_RETRIES} failed: {exc}", flush=True)
            if attempt < WEBHOOK_RETRIES:
                await asyncio.sleep(3 * attempt)
    return False


async def activate_bot(app: web.Application) -> None:
    """get_me + set_webhook после старта сервера (с retry)."""
    bot: Bot | None = app.get("bot")
    if not bot:
        return

    from services.video.ffmpeg_check import ensure_ffmpeg_available

    ensure_ffmpeg_available()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    ok = await _try_activate_once(bot)
    app["telegram_ready"] = ok
    if ok:
        return

    print(
        "WARNING: could not reach Telegram API from server. "
        "Set webhook manually: scripts/set_webhook.sh",
        flush=True,
    )


async def activate_bot_loop(app: web.Application) -> None:
    """Повторяет активацию, пока Timeweb не откроет доступ к api.telegram.org."""
    if app.get("telegram_ready"):
        return

    while not app.get("telegram_ready"):
        await asyncio.sleep(ACTIVATION_RETRY_SEC)
        bot: Bot | None = app.get("bot")
        if not bot:
            return
        print("=== Retrying Telegram activation ===", flush=True)
        if await _try_activate_once(bot):
            app["telegram_ready"] = True
            print("=== Bot activation done ===", flush=True)
            return
