from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, VideoContext


def _speech_blocks(activity_map: ActivityMap) -> list[tuple[float, float]]:
    pauses = sorted(activity_map.silent_ranges, key=lambda p: p.start)
    blocks: list[tuple[float, float]] = []
    cursor = 0.0

    for pause in pauses:
        if pause.start - cursor >= 1.0:
            blocks.append((cursor, pause.start))
        cursor = max(cursor, pause.end)

    if activity_map.duration_sec - cursor >= 1.0:
        blocks.append((cursor, activity_map.duration_sec))

    if not blocks and activity_map.duration_sec >= settings.min_clip_sec:
        blocks.append((0.0, activity_map.duration_sec))

    return blocks


def _block_score(start: float, end: float, activity_map: ActivityMap) -> float:
    score = 0.0
    count = 0
    for window in activity_map.windows:
        overlap_start = max(start, window.start)
        overlap_end = min(end, window.end)
        if overlap_end <= overlap_start:
            continue
        overlap = overlap_end - overlap_start
        local = 0.35 * window.speech_ratio + 0.25 * min(window.scene_changes / 3, 1.0)
        if not window.is_silent:
            local += 0.4
        score += local * overlap
        count += overlap
    return score / count if count else 0.3


def _title_for_block(index: int, start: float, end: float, context: VideoContext | None) -> str:
    if context and context.title:
        base = context.title[:80]
        return f"{base} ({int(start // 60)}:{int(start % 60):02d})"
    return f"Фрагмент {index}"


def detect_from_speech_blocks(
    activity_map: ActivityMap,
    context: VideoContext | None = None,
) -> HighlightResult:
    """Один непрерывный блок речи = одна мысль (естественные границы по паузам)."""
    blocks = _speech_blocks(activity_map)
    segments: list[HighlightSegment] = []

    for idx, (start, end) in enumerate(blocks, start=1):
        duration = end - start
        if duration < settings.min_clip_sec:
            continue
        if duration > settings.max_clip_sec:
            end = start + settings.max_clip_sec

        score = round(_block_score(start, end, activity_map), 2)
        segments.append(
            HighlightSegment(
                start_time=round(start, 2),
                end_time=round(end, 2),
                score=score,
                title=_title_for_block(idx, start, end, context),
                reason="Непрерывный фрагмент речи между паузами",
            )
        )

    segments.sort(key=lambda s: s.score, reverse=True)
    segments = segments[: settings.max_clips]
    recommended = min(max(1, len(segments)), settings.max_clips)

    theme = context.title if context and context.title else "Речевые фрагменты"
    return HighlightResult(
        recommended_clip_count=recommended,
        segments=segments,
        source="speech_blocks",
        video_theme=theme[:200],
    )
