#!/usr/bin/env python3
"""
Точка входа для Timeweb App Platform.
Сначала поднимает /health (для healthcheck), потом загружает бота в фоне.
"""
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
        print(f"WARNING: invalid PORT={raw!r}, using 8080", flush=True)
        return 8080


def _get_host() -> str:
    return (os.getenv("HOST") or "0.0.0.0").strip() or "0.0.0.0"


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def root_handler(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "highlight-bot"})


async def bootstrap_bot(app: web.Application) -> None:
    """Не блокирует on_startup — healthcheck проходит сразу."""
    asyncio.create_task(_bootstrap_bot_impl(app))


async def _bootstrap_bot_impl(app: web.Application) -> None:
    await asyncio.sleep(0.2)
    print("=== Bootstrap: loading bot ===", flush=True)
    try:
        from bot.bootstrap import init_bot

        await init_bot(app)
        print("=== Bootstrap: bot ready ===", flush=True)
    except Exception:
        print("=== Bootstrap: FAILED ===", flush=True)
        traceback.print_exc()


def main() -> None:
    host = _get_host()
    port = _get_port()
    print(f"=== HTTP server on {host}:{port} ===", flush=True)

    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    app.on_startup.append(bootstrap_bot)

    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
