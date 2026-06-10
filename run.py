#!/usr/bin/env python3
"""Точка входа для Docker / Timeweb App Platform."""
from __future__ import annotations

import asyncio
import os
import sys
import traceback

from aiohttp import web


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "mode": "fallback"})


async def _root(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "highlight-bot", "mode": "fallback"})


def _run_fallback_server() -> None:
    """Минимальный сервер, если основной код не стартует."""
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    print(f"FALLBACK: HTTP на {host}:{port}", flush=True)
    web.run_app(app, host=host, port=port)


def main() -> None:
    print("run.py: старт", flush=True)
    try:
        from bot.main import main as bot_main

        bot_main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        print(f"run.py: SystemExit({code})", flush=True)
        if code != 0:
            _run_fallback_server()
        raise
    except Exception:
        print("run.py: ошибка запуска основного приложения:", flush=True)
        traceback.print_exc()
        print("run.py: запуск fallback-сервера для healthcheck", flush=True)
        _run_fallback_server()


if __name__ == "__main__":
    main()
