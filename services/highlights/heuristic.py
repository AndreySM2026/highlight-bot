from __future__ import annotations

from config.settings import settings
from services.highlights.merge import finalize_highlight_result
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, VideoContext
from services.highlights.speech_blocks import detect_from_speech_blocks


def detect_highlights_heuristic(
    activity_map: ActivityMap,
    context: VideoContext | None = None,
) -> HighlightResult:
    raw = detect_from_speech_blocks(activity_map, context)
    return finalize_highlight_result(raw, activity_map)
