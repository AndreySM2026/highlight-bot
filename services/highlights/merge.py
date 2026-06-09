from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import HighlightResult, HighlightSegment


def _clamp_duration(segment: HighlightSegment) -> HighlightSegment:
    duration = segment.end_time - segment.start_time
    if duration > settings.max_clip_sec:
        segment.end_time = segment.start_time + settings.max_clip_sec
    if segment.end_time - segment.start_time < settings.min_clip_sec:
        segment.end_time = segment.start_time + settings.min_clip_sec
    return segment


def _overlaps(a: HighlightSegment, b: HighlightSegment) -> bool:
    return a.start_time < b.end_time and b.start_time < a.end_time


def normalize_segments(result: HighlightResult, duration_sec: float) -> HighlightResult:
    cleaned: list[HighlightSegment] = []
    for raw in sorted(result.segments, key=lambda s: s.score, reverse=True):
        seg = _clamp_duration(raw)
        if seg.start_time < 0:
            seg.start_time = 0.0
        if seg.end_time > duration_sec:
            seg.end_time = duration_sec
        if seg.end_time - seg.start_time < settings.min_clip_sec:
            continue
        if any(_overlaps(seg, existing) for existing in cleaned):
            continue
        cleaned.append(seg)
        if len(cleaned) >= settings.max_clips:
            break

    recommended = min(max(result.recommended_clip_count, 1), len(cleaned) or 1)
    recommended = min(recommended, settings.max_clips)

    return HighlightResult(
        recommended_clip_count=recommended,
        segments=cleaned,
        source=result.source,
    )
