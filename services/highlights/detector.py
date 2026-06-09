from __future__ import annotations

import structlog

from config.settings import settings
from services.highlights.heuristic import detect_highlights_heuristic
from services.highlights.merge import normalize_segments
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment
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
    )


async def detect_highlights(activity_map: ActivityMap) -> HighlightResult:
    client = TimewebClient()
    prompt = build_highlight_prompt(activity_map)

    try:
        response = await client.call_agent(prompt)
        data = extract_json(response)
        result = _parse_highlight_response(data)
        result = normalize_segments(result, activity_map.duration_sec)
        if result.segments:
            logger.info("highlights_detected", source="qwen", count=len(result.segments))
            return result
        logger.warning("highlights_empty_qwen_response")
    except TimewebError as exc:
        logger.warning("highlights_qwen_failed", error=str(exc))
    except Exception as exc:
        logger.warning("highlights_qwen_unexpected_error", error=str(exc))

    return detect_highlights_heuristic(activity_map)
