from __future__ import annotations

import json

from config.settings import settings
from services.highlights.schemas import ActivityMap, SpeechBlock, VideoContext
from services.highlights.speech_blocks import build_speech_blocks
from services.highlights.transcript import attach_transcript_to_blocks
from services.video.long_video import is_long_video


def _sample_blocks_for_prompt(blocks: list[SpeechBlock], max_blocks: int) -> list[SpeechBlock]:
    if len(blocks) <= max_blocks:
        return blocks
    step = len(blocks) / max_blocks
    sampled = [blocks[int(i * step)] for i in range(max_blocks)]
    return sampled


def build_highlight_prompt(activity_map: ActivityMap, context: VideoContext | None = None) -> str:
    blocks = build_speech_blocks(activity_map)
    if activity_map.transcript_segments:
        blocks = attach_transcript_to_blocks(blocks, activity_map.transcript_segments)
    if is_long_video(activity_map.duration_sec):
        blocks = _sample_blocks_for_prompt(blocks, settings.highlight_prompt_max_blocks)
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
Выбирай блоки по СМЫСЛУ: одна законченная мысль, которую зритель поймёт без контекста всего видео.
Клип должен работать как мини-трейлер: хук в начале, ясный вывод в конце, желание досмотреть полное видео.
НЕ выбирай блоки, которые начинаются с «и», «но», «что», «потому что» — это продолжение чужой мысли.
НЕ выбирай блоки, обрывающиеся на полуслове или без логического завершения.
"""

    return f"""Ты монтажёр коротких вертикальных клипов (Reels/Shorts) для русскоязычного видео.
{context_block}
КРИТИЧЕСКИ ВАЖНО — как нарезать:
- Нарезка ТОЛЬКО по speech_blocks (готовые фрагменты речи между паузами).
- Каждый клип = один block_id ИЛИ несколько СОСЕДНИХ block_id (если одна мысль длиннее одного блока).
- ЗАПРЕЩЕНО придумывать произвольные start_time/end_time — только целые блоки.
- Клип всегда начинается с НАЧАЛА реплики (start блока) и заканчивается на КОНЦЕ мысли (end блока).
- Одна идея = один клип. Не склеивай несмежные block_id.
- Зритель без просмотра всего ролика должен понять: о чём мысль, в чём инсайт, зачем смотреть дальше.
{transcript_hint}
Задача:
1. video_theme — главная тема видео (1–2 предложения).
2. Выбери лучшие блоки с самодостаточными мыслями (тезис понятен с первой секунды, мысль завершена в конце).
3. title — цепляющий заголовок сути (до 60 символов), не обрывок фразы.
4. reason — что зритель поймёт и почему захочет полное видео (1–2 предложения).
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
