from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, SilentRange


def _pause_duration(pause: SilentRange) -> float:
    return pause.end - pause.start


def _internal_pauses(segment: HighlightSegment, silent_ranges: list[SilentRange]) -> list[SilentRange]:
    """Паузы внутри сегмента — признак нескольких мыслей в одном клипе."""
    margin = settings.speech_split_edge_margin_sec
    min_pause = settings.speech_split_min_pause_sec
    internal: list[SilentRange] = []

    for pause in silent_ranges:
        if pause.start <= segment.start_time + margin:
            continue
        if pause.end >= segment.end_time - margin:
            continue
        if _pause_duration(pause) < min_pause:
            continue
        internal.append(pause)

    return sorted(internal, key=_pause_duration, reverse=True)


def _split_segment(segment: HighlightSegment, pause: SilentRange) -> list[HighlightSegment]:
    first = segment.model_copy(
        update={
            "end_time": round(pause.start + settings.speech_align_tail_sec, 2),
            "title": segment.title,
            "reason": f"{segment.reason} (часть 1)".strip(),
        }
    )
    second = segment.model_copy(
        update={
            "start_time": round(pause.end - settings.speech_align_preroll_sec, 2),
            "title": segment.title,
            "reason": f"{segment.reason} (часть 2)".strip(),
        }
    )
    return [first, second]


def split_multi_thought_segments(result: HighlightResult, activity_map: ActivityMap) -> HighlightResult:
    """Делит клипы, в которых несколько смысловых блоков (длинные паузы внутри)."""
    if not result.segments or not activity_map.silent_ranges:
        return result

    expanded: list[HighlightSegment] = []
    for segment in result.segments:
        parts = [segment]
        for _ in range(settings.speech_split_max_passes):
            next_parts: list[HighlightSegment] = []
            changed = False
            for part in parts:
                pauses = _internal_pauses(part, activity_map.silent_ranges)
                if not pauses:
                    next_parts.append(part)
                    continue
                split = _split_segment(part, pauses[0])
                next_parts.extend(split)
                changed = True
            parts = next_parts
            if not changed:
                break
        expanded.extend(parts)

    expanded.sort(key=lambda s: s.start_time)
    return result.model_copy(update={"segments": expanded})
