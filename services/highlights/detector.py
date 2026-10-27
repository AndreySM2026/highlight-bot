from __future__ import annotations

import asyncio

import structlog

from config.settings import settings
from services.highlights.merge import normalize_segments
from services.highlights.schemas import (
    ActivityMap,
    HighlightResult,
    HighlightSegment,
    SpeechBlock,
    VideoContext,
)
from services.highlights.speech_blocks import build_speech_blocks, segment_from_blocks
from services.highlights.transcript import attach_transcript_to_blocks
from services.highlights.verify import Candidate, verify_candidates
from services.timeweb.client import TimewebClient
from services.timeweb.json_parser import extract_json
from services.timeweb.prompts.highlight_detection import build_highlight_prompt

logger = structlog.get_logger(__name__)

AI_ATTEMPTS = 3
AI_RETRY_DELAY_SEC = 10


class HighlightsUnavailableError(RuntimeError):
    """ИИ недоступен или отвечает неразборчиво — отрывки не выбрать."""


class NoGoodHighlightsError(RuntimeError):
    """ИИ отработал, но законченных удачных мыслей не нашлось."""


def _block_range(item: dict) -> tuple[int, int] | None:
    """Диапазон фраз отрывка: start_block/end_block или (старый формат) список block_ids."""
    try:
        if "start_block" in item and "end_block" in item:
            first, last = int(item["start_block"]), int(item["end_block"])
        elif item.get("block_ids"):
            ids = [int(raw) for raw in item["block_ids"]]
            first, last = min(ids), max(ids)
        else:
            return None
    except (TypeError, ValueError):
        return None
    return (first, last) if first <= last else (last, first)


def _parse_candidates(data: dict, blocks: list[SpeechBlock]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for item in data.get("segments", []) or []:
        block_range = _block_range(item)
        if block_range is None:
            continue
        first, last = block_range
        if first < 0 or last >= len(blocks):
            continue
        # Берём ВСЕ фразы диапазона подряд: мысль из нескольких фраз не должна схлопываться в одну
        span = blocks[last].end - blocks[first].start
        if span > settings.max_clip_sec + 0.5:
            # Обрезать конец = оборвать мысль; лучше отказаться от отрывка
            logger.info("highlight_too_long_skipped", first=first, last=last, span=round(span, 1))
            continue
        try:
            score = min(1.0, max(0.0, float(item.get("score", 0.5))))
        except (TypeError, ValueError):
            score = 0.5
        candidates.append(
            Candidate(
                first=first,
                last=last,
                title=str(item.get("title", "Хайлайт")),
                reason=str(item.get("reason", "")),
                score=score,
            )
        )
    return candidates


def _to_result(
    candidates: list[Candidate],
    blocks: list[SpeechBlock],
    activity_map: ActivityMap,
    *,
    recommended: int,
    theme: str,
) -> HighlightResult:
    segments: list[HighlightSegment] = []
    for cand in candidates:
        seg = segment_from_blocks(
            blocks[cand.first : cand.last + 1],
            title=cand.title,
            reason=cand.reason,
            score=cand.score,
        )
        if seg:
            segments.append(seg)
    # Границы уже утвердил редактор — только убираем пересечения, лимит и добавляем запас по краям
    result = HighlightResult(
        recommended_clip_count=min(10, max(1, recommended)),
        segments=segments,
        source="qwen",
        video_theme=theme,
    )
    return normalize_segments(result, activity_map.duration_sec, blocks)


async def _call_with_retries(label: str, func):
    last_error: Exception | None = None
    for attempt in range(1, AI_ATTEMPTS + 1):
        try:
            return await func()
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"{label}_failed",
                attempt=attempt,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            if attempt < AI_ATTEMPTS:
                await asyncio.sleep(AI_RETRY_DELAY_SEC * attempt)
    raise HighlightsUnavailableError(f"{label}: {last_error}") from last_error


async def detect_highlights(
    activity_map: ActivityMap,
    context: VideoContext | None = None,
) -> HighlightResult:
    """
    Отрывки выбирает и затем проверяет ИИ. Без ИИ результат не выдаём:
    эвристика режет по паузам и захватывает чужие мысли.
    """
    client = TimewebClient()
    blocks = build_speech_blocks(activity_map)
    if activity_map.transcript_segments:
        blocks = attach_transcript_to_blocks(blocks, activity_map.transcript_segments)
    if not blocks or not any(b.text for b in blocks):
        raise HighlightsUnavailableError("нет расшифровки речи")
    prompt = build_highlight_prompt(activity_map, context)

    async def select() -> tuple[dict, list[Candidate]]:
        response = await client.call_agent(prompt)
        data = extract_json(response)
        candidates = _parse_candidates(data, blocks)
        if not candidates:
            raise ValueError(
                f"нет пригодных отрывков в ответе (сырых: {len(data.get('segments', []) or [])}); "
                f"начало ответа: {response[:200]!r}"
            )
        return data, candidates

    data, candidates = await _call_with_retries("highlights_select", select)
    logger.info("highlights_selected", count=len(candidates))

    async def verify() -> list[Candidate]:
        return await verify_candidates(client, candidates, blocks)

    approved = await _call_with_retries("highlights_verify", verify)
    if not approved:
        raise NoGoodHighlightsError("редактор отклонил все отрывки")

    result = _to_result(
        approved,
        blocks,
        activity_map,
        recommended=int(data.get("recommended_clip_count") or len(approved)),
        theme=str(data.get("video_theme", "")),
    )
    if not result.segments:
        raise NoGoodHighlightsError("после проверки не осталось отрывков нужной длины")
    logger.info(
        "highlights_detected",
        source="qwen",
        selected=len(candidates),
        approved=len(approved),
        count=len(result.segments),
        video_theme=result.video_theme[:120] if result.video_theme else None,
    )
    return result
