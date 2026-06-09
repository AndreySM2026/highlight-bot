from __future__ import annotations

import json

from services.highlights.schemas import ActivityMap


def build_highlight_prompt(activity_map: ActivityMap) -> str:
    payload = activity_map.model_dump()
    return f"""Проанализируй карту активности русскоязычного видео и найди самые яркие моменты для коротких вертикальных клипов.

Правила:
- Длительность каждого клипа: от 15 до 60 секунд.
- Клипы не должны сильно пересекаться.
- Учитывай пики громкости, плотность речи и смены сцен.
- recommended_clip_count — оптимальное число клипов (от 1 до 10).
- Ответь ТОЛЬКО валидным JSON без markdown и комментариев.

Формат ответа:
{{
  "recommended_clip_count": 5,
  "segments": [
    {{
      "start_time": 45.0,
      "end_time": 95.0,
      "score": 0.91,
      "title": "Краткое название момента",
      "reason": "Почему этот момент яркий"
    }}
  ]
}}

Карта активности:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
