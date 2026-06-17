from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, SpeechBlock
from services.highlights.snap import snap_all_segments_to_blocks
from services.highlights.speech_blocks import build_speech_blocks
from services.highlights.utterances import refine_highlight_result


def _clamp_duration(segment: HighlightSegment, blocks: list[SpeechBlock]) -> HighlightSegment:
    from services.highlights.utterances import fit_blocks_to_max_duration

    overlapping = [
        b
        for b in blocks
        if b.end > segment.start_time + 0.1 and b.start < segment.end_time - 0.1
    ]
    if overlapping:
        fitted = fit_blocks_to_max_duration(overlapping)
        if fitted:
            return segment.model_copy(update={"start_time": fitted[0].start, "end_time": fitted[-1].end})

    duration = segment.end_time - segment.start_time
    if duration > settings.max_clip_sec:
        segment = segment.model_copy(update={"end_time": segment.start_time + settings.max_clip_sec})
    return segment


def _overlaps(a: HighlightSegment, b: HighlightSegment) -> bool:
    return a.start_time < b.end_time and b.start_time < a.end_time


def normalize_segments(
    result: HighlightResult,
    duration_sec: float,
    blocks: list[SpeechBlock],
) -> HighlightResult:
    cleaned: list[HighlightSegment] = []
    for raw in sorted(result.segments, key=lambda s: s.score, reverse=True):
        seg = _clamp_duration(raw, blocks)
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

    cleaned.sort(key=lambda s: s.start_time)

    recommended = min(max(result.recommended_clip_count, 1), len(cleaned) or 1)
    recommended = min(recommended, settings.max_clips)

    return HighlightResult(
        recommended_clip_count=recommended,
        segments=cleaned,
        source=result.source,
        video_theme=result.video_theme,
    )


def finalize_highlight_result(result: HighlightResult, activity_map: ActivityMap) -> HighlightResult:
    blocks = build_speech_blocks(activity_map)
    result = snap_all_segments_to_blocks(result, blocks)
    result = refine_highlight_result(result, activity_map, blocks)
    return normalize_segments(result, activity_map.duration_sec, blocks)
