from __future__ import annotations

import json

from config.settings import settings
from services.highlights.schemas import ActivityMap, VideoContext
from services.highlights.speech_blocks import build_speech_blocks
from services.highlights.transcript import attach_transcript_to_blocks


def build_highlight_prompt(activity_map: ActivityMap, context: VideoContext | None = None) -> str:
    blocks = build_speech_blocks(activity_map)
    if activity_map.transcript_segments:
        blocks = attach_transcript_to_blocks(blocks, activity_map.transcript_segments)
    blocks_payload = [b.model_dump() for b in blocks]
    has_transcript = any(b.text for b in blocks)

    context_block = ""
    if context and (context.title or context.description):
        context_block = f"""
Контекст видео:
Название: {context.title}
Описание: {context.description[:1500]}
"""

    transcript_hint = ""
    if has_transcript:
        transcript_hint = """
У каждого speech_block есть поле text — расшифровка речи Whisper.
Выбирай блоки по СМЫСЛУ речи: законченная мысль, сильный тезис, инсайт, эмоция.
"""

    return f"""Ты монтажёр коротких вертикальных клипов (Reels/Shorts) для русскоязычного видео.
{context_block}
КРИТИЧЕСКИ ВАЖНО — как нарезать:
- Нарезка ТОЛЬКО по speech_blocks (готовые фрагменты речи между паузами).
- Каждый клип = один block_id ИЛИ несколько СОСЕДНИХ block_id (если одна мысль длиннее одного блока).
- ЗАПРЕЩЕНО придумывать произвольные start_time/end_time — только целые блоки.
- Клип всегда начинается с НАЧАЛА реплики (start блока) и заканчивается на КОНЦЕ мысли (end блока).
- Одна идея = один клип. Не склеивай несмежные block_id.
{transcript_hint}
Задача:
1. video_theme — главная тема видео (1–2 предложения).
2. Выбери лучшие блоки с законченными мыслями (тезис понятен с первой секунды).
3. title — заголовок сути (до 60 символов).
4. reason — что зритель поймёт, если посмотрит только этот клип (1–2 предложения).
5. Длительность определяется блоками ({settings.min_clip_sec}–{settings.max_clip_sec} сек).

recommended_clip_count = число лучших идей (1–10).

Ответь ТОЛЬКО валидным JSON без markdown.

Формат:
{{
  "video_theme": "Главная тема",
  "recommended_clip_count": 3,
  "segments": [
    {{
      "block_ids": [2],
      "score": 0.91,
      "title": "Заголовок идеи",
      "reason": "Зритель поймёт: ..."
    }},
    {{
      "block_ids": [5, 6],
      "score": 0.85,
      "title": "Другая идея",
      "reason": "..."
    }}
  ]
}}

speech_blocks (id, start, end, duration, text — секунды и расшифровка речи):
{json.dumps(blocks_payload, ensure_ascii=False, indent=2)}
"""
