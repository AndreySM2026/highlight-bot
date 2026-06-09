from __future__ import annotations

#!/usr/bin/env python3
"""Проверка подключения к Timeweb AI-агенту."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.timeweb.client import TimewebClient
from services.timeweb.json_parser import extract_json


async def main() -> None:
    client = TimewebClient()
    response = await client.call_agent('Верни только JSON: {"status": "ok"}')
    print("Raw response:")
    print(response)
    print("\nParsed JSON:")
    print(extract_json(response))


if __name__ == "__main__":
    asyncio.run(main())
