from __future__ import annotations

#!/usr/bin/env python3
"""Локальный тест эвристики хайлайтов без Timeweb."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.highlights.heuristic import detect_highlights_heuristic
from services.highlights.schemas import ActivityMap


async def main() -> None:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "activity_map.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    activity_map = ActivityMap.model_validate(data)
    result = detect_highlights_heuristic(activity_map)
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
