"""
Вычитка субтитров ИИ перед наложением.

Whisper на русском путает окончания, имена и термины («из традиционной кухне», «ангросфарм»).
Отправляем ИИ только фразы, попадающие в выбранные клипы, получаем исправленный текст
с теми же id строк — тайминги не меняются. Без успешной вычитки субтитры не накладываем.
"""

from __future__ import annotations

import asyncio
import json

import structlog

from services.highlights.schemas import HighlightSegment, TranscriptSegment
from services.timeweb.client import TimewebClient
from services.timeweb.exceptions import TimewebModerationError, TimewebQuotaError
from services.timeweb.json_parser import extract_json
from services.timeweb.prompts.common import JSON_RULES, retry_note

logger = structlog.get_logger(__name__)

BATCH_LINES = 60
ATTEMPTS = 3
RETRY_DELAY_SEC = 10


class ProofreadError(RuntimeError):
    """ИИ не смог вычитать субтитры — накладывать сырой текст нельзя."""


def _build_prompt(lines: dict[int, str], theme: str, titles: list[str]) -> str:
    payload = [{"id": i, "text": t} for i, t in lines.items()]
    return f"""Ты — корректор субтитров для подкаста о маркетинге «Медиамикс».
Тема выпуска: {theme or "маркетинг"}.
Темы клипов: {"; ".join(titles)}.

Ниже строки автоматической расшифровки русской речи (Whisper). В них бывают ошибки распознавания:
неверные окончания и падежи, слитые или разорванные слова, искажённые названия брендов, компаний,
имена и профессиональные термины (SMM, CTR, ретейл, агробизнес, инфлюенсер и т. п.).

Исправь каждую строку:
- орфография, пунктуация, падежи и согласование — по нормам русского языка;
- явно неверно распознанные слова — на те, что по смыслу сказал спикер;
- НЕ пересказывай и НЕ сокращай: сохраняй слова и порядок спикера, разговорный стиль допустим;
- слова-паразиты («ну», «вот», «как бы», «э-э») и повторы убери, если они мешают читать;
- id и количество строк не меняй, строки не объединяй и не дели.

{JSON_RULES}
Формат:
{{"lines": [{{"id": 0, "text": "исправленный текст"}}]}}

Строки:
{json.dumps(payload, ensure_ascii=False)}
"""


def _validate(original: dict[int, str], data: dict) -> tuple[dict[int, str], list[int]]:
    """Годные исправленные строки и id строк, которые корректор пропустил или переписал подозрительно."""
    fixed: dict[int, str] = {}
    for item in data.get("lines", []) or []:
        try:
            line_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(item.get("text", "")).strip()
        if line_id not in original or not text:
            continue
        source = original[line_id]
        ratio = len(text) / max(1, len(source))
        # Короткие строки законно меняются сильно («Ну, вот, да» → «Да»), их не проверяем
        if len(source) >= 15 and not 0.3 <= ratio <= 2.2:
            continue
        fixed[line_id] = text
    bad = sorted(set(original) - set(fixed))
    return fixed, bad


async def _request(client: TimewebClient, lines: dict[int, str], theme: str, titles: list[str], attempt: int):
    response = await client.call_agent(_build_prompt(lines, theme, titles) + retry_note(attempt))
    return _validate(lines, extract_json(response))


async def _proofread_moderated(
    client: TimewebClient,
    lines: dict[int, str],
    theme: str,
    titles: list[str],
) -> dict[int, str]:
    """Модерация отклонила пакет: делим пополам; строки, отклонённые и в малой группе, оставляем как есть."""
    if len(lines) <= 4:
        logger.warning("proofread_moderated_kept_original", ids=sorted(lines))
        return dict(lines)
    ids = sorted(lines)
    result: dict[int, str] = {}
    for half in (ids[: len(ids) // 2], ids[len(ids) // 2 :]):
        result.update(await _proofread_batch(client, {i: lines[i] for i in half}, theme, titles))
    return result


async def _proofread_batch(
    client: TimewebClient,
    lines: dict[int, str],
    theme: str,
    titles: list[str],
) -> dict[int, str]:
    """
    Одна-две «плохие» строки не должны браковать весь пакет: годные принимаем,
    плохие переспрашиваем отдельно, а если и это не помогло — оставляем исходный текст строки.
    """
    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            try:
                fixed, bad = await _request(client, lines, theme, titles, attempt)
            except TimewebModerationError:
                return await _proofread_moderated(client, lines, theme, titles)
            if len(bad) > max(1, len(lines) // 10):
                raise ValueError(f"корректор не справился с {len(bad)} из {len(lines)} строк: {bad[:10]}")
            if bad:
                retry_fixed, still_bad = await _request(client, {i: lines[i] for i in bad}, theme, titles, attempt + 1)
                fixed.update(retry_fixed)
                for i in still_bad:
                    fixed[i] = lines[i]
                logger.info("proofread_lines_kept_original", ids=still_bad, retried=len(bad))
            return fixed
        except TimewebQuotaError:
            raise  # лимит токенов исчерпан — повтор только сожжёт время
        except Exception as exc:
            last_error = exc
            logger.warning("proofread_failed", attempt=attempt, error=str(exc)[:300])
            if attempt < ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SEC * attempt)
    raise ProofreadError(str(last_error)) from last_error


async def proofread_transcript(
    transcript: list[TranscriptSegment],
    segments: list[HighlightSegment],
    *,
    theme: str = "",
) -> list[TranscriptSegment]:
    """Возвращает транскрипт, где строки внутри клипов вычитаны ИИ."""
    needed = [
        i
        for i, seg in enumerate(transcript)
        if any(seg.end > clip.start_time and seg.start < clip.end_time for clip in segments)
    ]
    if not needed:
        return transcript

    client = TimewebClient()
    titles = [s.title for s in segments]
    corrected = list(transcript)
    changed = 0
    for offset in range(0, len(needed), BATCH_LINES):
        batch_ids = needed[offset : offset + BATCH_LINES]
        lines = {i: transcript[i].text for i in batch_ids}
        fixed = await _proofread_batch(client, lines, theme, titles)
        for i, text in fixed.items():
            if text != transcript[i].text:
                changed += 1
            corrected[i] = transcript[i].model_copy(update={"text": text})

    logger.info("proofread_done", lines=len(needed), changed=changed)
    return corrected
