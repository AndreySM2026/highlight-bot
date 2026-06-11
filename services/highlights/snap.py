from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import HighlightResult, HighlightSegment, SpeechBlock
from services.highlights.speech_blocks import segment_from_blocks


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _blocks_for_segment(segment: HighlightSegment, blocks: list[SpeechBlock]) -> list[SpeechBlock]:
    """Подбирает один или несколько соседних блоков, покрывающих сегмент."""
    if not blocks:
        return []

    containing = [b for b in blocks if b.start <= segment.start_time <= b.end + 0.3]
    if containing:
        anchor = min(containing, key=lambda b: segment.start_time - b.start)
    else:
        before = [b for b in blocks if b.end <= segment.start_time + 0.5]
        if before:
            anchor = max(before, key=lambda b: b.end)
        else:
            anchor = min(blocks, key=lambda b: abs(b.start - segment.start_time))

    start_idx = anchor.id
    chosen = [blocks[start_idx]]
    total_duration = chosen[0].duration

    idx = start_idx + 1
    while idx < len(blocks) and total_duration < settings.min_clip_sec:
        nxt = blocks[idx]
        if nxt.start - chosen[-1].end > settings.speech_min_pause_sec + 0.2:
            break
        chosen.append(nxt)
        total_duration += nxt.duration
        idx += 1

    while idx < len(blocks) and total_duration < (segment.end_time - segment.start_time) * 0.85:
        nxt = blocks[idx]
        if nxt.start - chosen[-1].end > settings.speech_min_pause_sec + 0.2:
            break
        if total_duration + nxt.duration > settings.max_clip_sec:
            break
        chosen.append(nxt)
        total_duration += nxt.duration
        idx += 1

    return chosen


def snap_segment_to_blocks(
    segment: HighlightSegment,
    blocks: list[SpeechBlock],
) -> HighlightSegment | None:
    if not blocks:
        return segment

    best_single: SpeechBlock | None = None
    best_overlap = 0.0
    for block in blocks:
        ov = _overlap(segment.start_time, segment.end_time, block.start, block.end)
        if ov > best_overlap:
            best_overlap = ov
            best_single = block

    if best_single and best_overlap >= settings.speech_min_block_sec * 0.5:
        return segment.model_copy(
            update={
                "start_time": best_single.start,
                "end_time": min(best_single.end, best_single.start + settings.max_clip_sec),
            }
        )

    chosen = _blocks_for_segment(segment, blocks)
    if not chosen:
        return segment

    snapped = segment_from_blocks(
        chosen,
        title=segment.title,
        reason=segment.reason,
        score=segment.score,
    )
    return snapped or segment


def snap_all_segments_to_blocks(
    result: HighlightResult,
    blocks: list[SpeechBlock],
) -> HighlightResult:
    if not blocks:
        return result

    snapped: list[HighlightSegment] = []
    for segment in result.segments:
        fixed = snap_segment_to_blocks(segment, blocks)
        if fixed and fixed.end_time - fixed.start_time >= settings.min_clip_sec:
            snapped.append(fixed)

    return result.model_copy(update={"segments": snapped})
