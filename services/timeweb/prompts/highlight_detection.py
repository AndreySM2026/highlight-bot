from __future__ import annotations

import json

from config.settings import settings
from services.highlights.schemas import ActivityMap, VideoContext


def build_highlight_prompt(activity_map: ActivityMap, context: VideoContext | None = None) -> str:
    payload = activity_map.model_dump()
    context_block = ""
    if context and (context.title or context.description):
        context_block = f"""
Контекст видео (название и описание с Rutube):
Название: {context.title}
Описание: {context.description[:1500]}
"""

    return f"""Ты монтажёр коротких вертикальных клипов (Reels/Shorts) для русскоязычного видео.
{context_block}
ЗАДАЧА:

1. Определи главную тему видео (video_theme).
2. Найди отдельные ЗАКОНЧЕННЫЕ идеи/тезисы — каждая должна быть понятна без просмотра всего ролика.
3. На каждую идею — ровно ОДИН клип. Нельзя склеивать две разные мысли.
4. title — короткий заголовок сути (как заголовок поста, до 60 символов).
5. reason — в 1–2 предложениях: какую мысль зритель поймёт из этого клипа.

Границы по времени:
- start_time — сразу после паузы (silence_end), где начинается эта идея.
- end_time — на паузе (silence_start), когда идея закончена.
- Длительность ЛЮБАЯ, по смыслу: от {settings.min_clip_sec} до {settings.max_clip_sec} секунд.
- Не растягивай клип до минуты, если мысль уложилась в 20–30 секунд.
- Не обрезай, если мысль ещё не раскрыта.

recommended_clip_count = число лучших самостоятельных идей (1–10).

Ответь ТОЛЬКО валидным JSON без markdown.

Формат:
{{
  "video_theme": "Главная тема",
  "ideas": [{{"title": "...", "summary": "..."}}],
  "recommended_clip_count": 3,
  "segments": [
    {{
      "start_time": 12.0,
      "end_time": 38.0,
      "score": 0.91,
      "title": "Заголовок идеи",
      "reason": "Зритель поймёт: ..."
    }}
  ]
}}

Карта активности (silent_ranges — паузы в речи, windows — активность по 20 сек):
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
