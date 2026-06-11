from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import (
    ActivityMap,
    HighlightResult,
    HighlightSegment,
    SilentRange,
    SpeechBlock,
    VideoContext,
)


def _significant_pauses(silent_ranges: list[SilentRange]) -> list[SilentRange]:
    min_pause = settings.speech_min_pause_sec
    return sorted(
        [p for p in silent_ranges if p.end - p.start >= min_pause],
        key=lambda p: p.start,
    )


def build_speech_blocks(activity_map: ActivityMap) -> list[SpeechBlock]:
    """
    Блоки речи между значимыми паузами.
    Каждый блок — потенциально одна законченная мысль/реплика.
    """
    pauses = _significant_pauses(activity_map.silent_ranges)
    raw: list[tuple[float, float]] = []
    cursor = 0.0

    for pause in pauses:
        if pause.start - cursor >= settings.speech_min_block_sec:
            raw.append((cursor, pause.start))
        cursor = max(cursor, pause.end)

    if activity_map.duration_sec - cursor >= settings.speech_min_block_sec:
        raw.append((cursor, activity_map.duration_sec))

    if not raw and activity_map.duration_sec >= settings.min_clip_sec:
        raw.append((0.0, activity_map.duration_sec))

    blocks: list[SpeechBlock] = []
    for idx, (start, end) in enumerate(raw):
        blocks.append(
            SpeechBlock(
                id=idx,
                start=round(start, 2),
                end=round(end, 2),
                duration=round(end - start, 2),
            )
        )
    return blocks


def _block_score(block: SpeechBlock, activity_map: ActivityMap) -> float:
    score = 0.0
    weight = 0.0
    for window in activity_map.windows:
        overlap_start = max(block.start, window.start)
        overlap_end = min(block.end, window.end)
        if overlap_end <= overlap_start:
            continue
        overlap = overlap_end - overlap_start
        local = 0.35 * window.speech_ratio + 0.25 * min(window.scene_changes / 3, 1.0)
        if not window.is_silent:
            local += 0.4
        score += local * overlap
        weight += overlap
    return score / weight if weight else 0.3


def segment_from_blocks(
    blocks: list[SpeechBlock],
    *,
    title: str,
    reason: str,
    score: float,
) -> HighlightSegment | None:
    if not blocks:
        return None
    start = blocks[0].start
    end = blocks[-1].end
    duration = end - start
    if duration < settings.min_clip_sec:
        return None
    if duration > settings.max_clip_sec:
        end = start + settings.max_clip_sec
    return HighlightSegment(
        start_time=start,
        end_time=round(end, 2),
        score=score,
        title=title,
        reason=reason,
    )


def detect_from_speech_blocks(
    activity_map: ActivityMap,
    context: VideoContext | None = None,
) -> HighlightResult:
    blocks = build_speech_blocks(activity_map)
    segments: list[HighlightSegment] = []

    for block in blocks:
        if block.duration < settings.min_clip_sec:
            continue
        title = f"Реплика {block.id + 1}"
        if context and context.title:
            title = f"{context.title[:50]}… ({int(block.start // 60)}:{int(block.start % 60):02d})"
        seg = segment_from_blocks(
            [block],
            title=title,
            reason="Целый фрагмент речи от паузы до паузы",
            score=round(_block_score(block, activity_map), 2),
        )
        if seg:
            segments.append(seg)

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
