from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import ActivityMap, SpeechBlock, VideoContext
from services.highlights.speech_blocks import build_speech_blocks
from services.highlights.transcript import attach_transcript_to_blocks


def _block_line(block: SpeechBlock) -> str:
    text = block.text or "(речь без расшифровки)"
    return f"[{block.id}] {block.start:.1f}–{block.end:.1f} ({block.duration:.0f}с) {text}"


def _fit_blocks_to_budget(blocks: list[SpeechBlock], max_chars: int) -> list[SpeechBlock]:
    """
    Если расшифровка не влезает в промпт — берём равномерно разнесённые ОКНА подряд идущих фраз.
    Отдельные разрозненные фразы бесполезны: из них нельзя собрать законченную мысль.
    """
    total = sum(len(_block_line(b)) + 1 for b in blocks)
    if total <= max_chars:
        return blocks

    keep_ratio = max_chars / total
    window = 12
    windows_total = max(1, len(blocks) // window)
    windows_keep = max(1, int(windows_total * keep_ratio))
    step = windows_total / windows_keep
    picked: list[SpeechBlock] = []
    for i in range(windows_keep):
        start = int(i * step) * window
        picked.extend(blocks[start : start + window])
    return picked


def build_highlight_prompt(activity_map: ActivityMap, context: VideoContext | None = None) -> str:
    blocks = build_speech_blocks(activity_map)
    if activity_map.transcript_segments:
        blocks = attach_transcript_to_blocks(blocks, activity_map.transcript_segments)
    blocks = _fit_blocks_to_budget(blocks, settings.highlight_prompt_max_chars)
    transcript_lines = "\n".join(_block_line(b) for b in blocks)

    context_block = ""
    if context and (context.title or context.description):
        context_block = f"""
Выпуск:
Название: {context.title}
Описание: {context.description[:1500]}
"""

    min_sec = settings.min_clip_sec
    max_sec = settings.max_clip_sec

    return f"""Ты — редактор коротких вертикальных видео (Reels, Shorts, TikTok, VK Клипы) для подкаста
«Медиамикс» — шоу о маркетинге. Аудитория — маркетологи, предприниматели, SMM-специалисты.
Из расшифровки длинного выпуска выбери отрывки, которые сами по себе соберут просмотры и подписки.
{context_block}
Расшифровка разбита на фразы. Формат строки: [номер] начало–конец (длительность) текст.

КАК ВЫБИРАТЬ ОТРЫВОК
1. Отрывок — это НЕПРЕРЫВНЫЙ диапазон фраз: с фразы start_block по фразу end_block включительно.
2. Начало — хук. Первая фраза сама по себе цепляет и понятна без контекста:
   сильный тезис, неожиданная цифра, провокационный вопрос, «главная ошибка…», «никто не говорит, что…».
   НЕЛЬЗЯ начинать с середины мысли: с «и», «но», «а», «поэтому», «вот», «это», «он/она/они»,
   «как я уже сказал», с ответа на вопрос, который в отрывок не попал.
   Если тезис начинается с вопроса ведущего — включи вопрос в отрывок.
3. Конец — завершённая мысль: вывод, пуанта, совет, итоговая цифра. Не обрывай на перечислении,
   на «во-первых» без «во-вторых», на середине истории или до ответа на поставленный вопрос.
   Лучше захватить ещё одну фразу, чтобы мысль закончилась.
4. Длительность от {min_sec} до {max_sec} секунд, лучше всего 25–60. Считай по времени начала первой
   и конца последней фразы. Если законченная мысль не укладывается в лимит — не бери её.
5. Ценность для зрителя: конкретный инсайт, кейс с цифрами, практический совет, спорное мнение,
   смешной или эмоциональный момент. Пропускай приветствия, представление гостей, рекламу, болтовню
   «ни о чём», технические паузы и отрывки, которые понятны только тем, кто смотрел выпуск целиком.
6. Отрывки не пересекаются. Каждый — про отдельную мысль.

ЧТО ВЕРНУТЬ
- video_theme: о чём выпуск, 1–2 предложения.
- segments: отрывки от лучшего к худшему (до 10 штук). Для каждого:
  - start_block, end_block — номера первой и последней фразы;
  - score — от 0 до 1, насколько отрывок сильный для коротких видео;
  - title — цепляющий заголовок до 60 символов, как обложка шортса (не обрывок фразы);
  - reason — одно предложение: чем отрывок зацепит зрителя.
- recommended_clip_count: сколько отрывков действительно стоит публиковать (1–10).

Ответь ТОЛЬКО валидным JSON без markdown, строго в таком формате:
{{
  "video_theme": "…",
  "recommended_clip_count": 3,
  "segments": [
    {{"start_block": 41, "end_block": 47, "score": 0.92, "title": "…", "reason": "…"}}
  ]
}}

РАСШИФРОВКА:
{transcript_lines}
"""
