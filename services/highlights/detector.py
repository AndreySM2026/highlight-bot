from __future__ import annotations

import structlog

from config.settings import settings
from services.highlights.heuristic import detect_highlights_heuristic
from services.highlights.merge import finalize_highlight_result
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, VideoContext
from services.highlights.speech_blocks import build_speech_blocks, detect_from_speech_blocks, segment_from_blocks
from services.timeweb.client import TimewebClient
from services.timeweb.exceptions import TimewebError
from services.timeweb.json_parser import extract_json
from services.timeweb.prompts.highlight_detection import build_highlight_prompt

logger = structlog.get_logger(__name__)

AI_ATTEMPTS = 2


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


def _parse_highlight_response(data: dict, activity_map: ActivityMap) -> HighlightResult:
    blocks = build_speech_blocks(activity_map)
    blocks_by_id = {b.id: b for b in blocks}
    segments: list[HighlightSegment] = []

    for item in data.get("segments", []):
        title = str(item.get("title", "Хайлайт"))
        reason = str(item.get("reason", ""))
        score = min(1.0, max(0.0, float(item.get("score", 0.5))))

        block_range = _block_range(item)
        if block_range is not None:
            first, last = block_range
            # Берём ВСЕ фразы диапазона подряд: мысль из нескольких фраз не должна схлопываться в одну
            chosen = [blocks_by_id[i] for i in range(first, last + 1) if i in blocks_by_id]
            if not chosen:
                continue
            span = chosen[-1].end - chosen[0].start
            if span > settings.max_clip_sec + 0.5:
                # Обрезать конец = оборвать мысль; лучше отказаться от отрывка
                logger.info("highlight_too_long_skipped", first=first, last=last, span=round(span, 1))
                continue
            seg = segment_from_blocks(chosen, title=title, reason=reason, score=score)
            if seg:
                segments.append(seg)
            continue

        if "start_time" in item and "end_time" in item:
            segments.append(
                HighlightSegment(
                    start_time=float(item["start_time"]),
                    end_time=float(item["end_time"]),
                    score=score,
                    title=title,
                    reason=reason,
                )
            )

    return HighlightResult(
        recommended_clip_count=min(10, max(1, int(data.get("recommended_clip_count") or max(1, len(segments) // 2)))),
        segments=segments,
        source="qwen",
        video_theme=str(data.get("video_theme", "")),
    )


async def detect_highlights(
    activity_map: ActivityMap,
    context: VideoContext | None = None,
) -> HighlightResult:
    client = TimewebClient()
    prompt = build_highlight_prompt(activity_map, context)

    # Разовые сбои (ошибка API, обрезанный JSON, таймаут) не должны сразу отправлять в эвристику
    for attempt in range(1, AI_ATTEMPTS + 1):
        try:
            response = await client.call_agent(prompt)
            data = extract_json(response)
            raw = _parse_highlight_response(data, activity_map)
            result = finalize_highlight_result(raw, activity_map)
            if result.segments:
                logger.info(
                    "highlights_detected",
                    source="qwen",
                    attempt=attempt,
                    count=len(result.segments),
                    video_theme=result.video_theme[:120] if result.video_theme else None,
                )
                return result
            logger.warning(
                "highlights_empty_qwen_response",
                attempt=attempt,
                raw_segments=len(data.get("segments", []) or []),
                parsed_segments=len(raw.segments),
                response_head=response[:300],
            )
        except TimewebError as exc:
            logger.warning("highlights_qwen_failed", attempt=attempt, error=str(exc)[:500])
        except Exception as exc:
            logger.warning(
                "highlights_qwen_unexpected_error",
                attempt=attempt,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )

    speech = detect_from_speech_blocks(activity_map, context)
    speech = finalize_highlight_result(speech, activity_map)
    if speech.segments:
        logger.info("highlights_detected", source="speech_blocks", count=len(speech.segments))
        return speech

    return detect_highlights_heuristic(activity_map, context)
