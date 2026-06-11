from __future__ import annotations

import json

from config.settings import settings
from services.highlights.schemas import ActivityMap


def build_highlight_prompt(activity_map: ActivityMap) -> str:
    payload = activity_map.model_dump()
    return f"""Ты монтажёр коротких вертикальных клипов (Reels/Shorts) для русскоязычного видео.

ЗАДАЧА В ДВА ШАГА:

Шаг 1 — понять видео целиком:
- Определи главную тему видео (поле video_theme, 1–2 предложения).
- Выдели отдельные законченные ИДЕИ/ТЕЗИСЫ, о которых говорят (поле ideas).
- Каждая идея = одна мысль, которую можно понять без просмотра всего видео.

Шаг 2 — нарезка:
- На КАЖДУЮ идею — ровно ОДИН клип (один элемент segments).
- ЗАПРЕЩЕНО объединять две разные идеи в один клип.
- ЗАПРЕЩЕНО обрывать идею на полуслове или до вывода.
- Клип должен полностью раскрывать одну мысль: тезис → аргумент/пример → вывод.
- start_time — сразу после паузы (silence_end), когда начинается эта идея.
- end_time — на паузе после завершения этой же идеи (silence_start).
- Длительность клипа: {settings.min_clip_sec}–{settings.max_clip_sec} секунд.
- Клипы не должны сильно пересекаться.
- recommended_clip_count = число лучших самостоятельных идей (1–10).

Ответь ТОЛЬКО валидным JSON без markdown.

Формат:
{{
  "video_theme": "Главная тема всего видео",
  "ideas": [
    {{
      "title": "Название одной идеи",
      "summary": "Суть идеи в 1–2 предложениях"
    }}
  ],
  "recommended_clip_count": 4,
  "segments": [
    {{
      "start_time": 45.0,
      "end_time": 95.0,
      "score": 0.91,
      "title": "Название идеи (как в ideas)",
      "reason": "Какую одну мысль раскрывает клип и почему она самостоятельна"
    }}
  ]
}}

Карта активности (windows — окна по 20 сек, silent_ranges — паузы в речи):
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
