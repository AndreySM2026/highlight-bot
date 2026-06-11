from __future__ import annotations

import structlog

from services.highlights.heuristic import detect_highlights_heuristic
from services.highlights.merge import finalize_highlight_result
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, VideoContext
from services.highlights.speech_blocks import detect_from_speech_blocks
from services.timeweb.client import TimewebClient
from services.timeweb.exceptions import TimewebError
from services.timeweb.json_parser import extract_json
from services.timeweb.prompts.highlight_detection import build_highlight_prompt

logger = structlog.get_logger(__name__)


def _parse_highlight_response(data: dict) -> HighlightResult:
    segments = [
        HighlightSegment(
            start_time=float(item["start_time"]),
            end_time=float(item["end_time"]),
            score=float(item.get("score", 0.5)),
            title=str(item.get("title", "Хайлайт")),
            reason=str(item.get("reason", "")),
        )
        for item in data.get("segments", [])
    ]
    return HighlightResult(
        recommended_clip_count=int(data.get("recommended_clip_count", max(1, len(segments) // 2))),
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

    try:
        response = await client.call_agent(prompt)
        data = extract_json(response)
        result = _parse_highlight_response(data)
        result = finalize_highlight_result(result, activity_map)
        if result.segments:
            logger.info(
                "highlights_detected",
                source="qwen",
                count=len(result.segments),
                video_theme=result.video_theme[:120] if result.video_theme else None,
            )
            return result
        logger.warning("highlights_empty_qwen_response")
    except TimewebError as exc:
        logger.warning("highlights_qwen_failed", error=str(exc))
    except Exception as exc:
        logger.warning("highlights_qwen_unexpected_error", error=str(exc))

    speech = detect_from_speech_blocks(activity_map, context)
    speech = finalize_highlight_result(speech, activity_map)
    if speech.segments:
        logger.info("highlights_detected", source="speech_blocks", count=len(speech.segments))
        return speech

    return detect_highlights_heuristic(activity_map, context)
