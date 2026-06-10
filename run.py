#!/usr/bin/env python3
"""Точка входа для Timeweb App Platform."""
from __future__ import annotations

import asyncio
import os
import sys
import traceback

print("=== Highlight Bot: run.py START ===", flush=True)

from aiohttp import web  # noqa: E402


def _get_port() -> int:
    raw = os.getenv("PORT", "8080").strip()
    try:
        return int(raw) if raw else 8080
    except ValueError:
        return 8080


def _get_host() -> str:
    return (os.getenv("HOST") or "0.0.0.0").strip() or "0.0.0.0"


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def root_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "highlight-bot"})


async def activate_bot_task(app: web.Application) -> None:
    """Не блокирует on_startup."""
    asyncio.create_task(_activate_bot_impl(app))


async def status_handler(request: web.Request) -> web.Response:
    from config.settings import settings

    payload: dict = {
        "status": "ok",
        "service": "highlight-bot",
        "telegram_ready": bool(request.app.get("telegram_ready")),
        "webhook_path": settings.webhook_path if settings.webhook_secret else None,
        "webhook_url": settings.full_webhook_url if settings.webhook_url else None,
    }

    bot = request.app.get("bot")
    if bot and request.app.get("telegram_ready"):
        try:
            wh = await bot.get_webhook_info()
            payload["telegram_webhook"] = {
                "url": wh.url,
                "pending_update_count": wh.pending_update_count,
                "last_error_message": wh.last_error_message,
                "last_error_date": wh.last_error_date,
            }
        except Exception as exc:
            payload["telegram_webhook_error"] = str(exc)

    return web.json_response(payload)


async def _activate_bot_impl(app: web.Application) -> None:
    await asyncio.sleep(0.5)
    print("=== Activating bot (Telegram API) ===", flush=True)
    try:
        from bot.bootstrap import activate_bot, activate_bot_loop

        await activate_bot(app)
        if app.get("telegram_ready"):
            print("=== Bot activation done ===", flush=True)
        else:
            asyncio.create_task(activate_bot_loop(app))
    except Exception:
        print("=== Bot activation FAILED ===", flush=True)
        traceback.print_exc()


def main() -> None:
    host = _get_host()
    port = _get_port()
    print(f"=== Configuring app, port={port} ===", flush=True)

    app = web.Application()
    app["telegram_ready"] = False
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/status", status_handler)

    from bot.bootstrap import configure_app

    configure_app(app)

    app.on_startup.append(activate_bot_task)

    print(f"=== Starting HTTP server on {host}:{port} ===", flush=True)
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
