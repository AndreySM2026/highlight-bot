from __future__ import annotations

from config.settings import settings
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment, SilentRange


def _snap_start(start_time: float, silent_ranges: list[SilentRange]) -> float:
    """Сдвигает начало к ближайшей паузе перед речью (начало фразы)."""
    lookback = settings.speech_align_lookback_sec
    preroll = settings.speech_align_preroll_sec
    best: float | None = None

    for pause in silent_ranges:
        if pause.end <= start_time and start_time - pause.end <= lookback:
            candidate = max(0.0, pause.end - preroll)
            if best is None or pause.end > best:
                best = pause.end

    if best is not None:
        return max(0.0, best - preroll)

    return max(0.0, start_time - settings.speech_align_fallback_pad_sec)


def _snap_end(end_time: float, silent_ranges: list[SilentRange], duration_sec: float) -> float:
    """Продлевает конец до паузы после фразы."""
    lookahead = settings.speech_align_lookahead_sec
    best: float | None = None

    for pause in silent_ranges:
        if end_time <= pause.start <= end_time + lookahead:
            candidate = min(duration_sec, pause.start + settings.speech_align_tail_sec)
            if best is None or pause.start < best:
                best = candidate

    if best is not None:
        return best

    return min(duration_sec, end_time + settings.speech_align_fallback_tail_sec)


def _clamp_segment(segment: HighlightSegment, duration_sec: float) -> HighlightSegment:
    start = segment.start_time
    end = segment.end_time
    length = end - start

    if length > settings.max_clip_sec:
        end = start + settings.max_clip_sec
    if end - start < settings.min_clip_sec:
        end = min(duration_sec, start + settings.min_clip_sec)
    if end > duration_sec:
        shift = end - duration_sec
        start = max(0.0, start - shift)
        end = duration_sec

    return segment.model_copy(update={"start_time": round(start, 2), "end_time": round(end, 2)})


def align_segments_to_speech(result: HighlightResult, activity_map: ActivityMap) -> HighlightResult:
    if not result.segments:
        return result

    silent_ranges = activity_map.silent_ranges
    aligned: list[HighlightSegment] = []

    for segment in result.segments:
        start = _snap_start(segment.start_time, silent_ranges)
        end = _snap_end(segment.end_time, silent_ranges, activity_map.duration_sec)
        if end <= start:
            end = min(activity_map.duration_sec, start + settings.min_clip_sec)
        aligned.append(
            segment.model_copy(
                update={
                    "start_time": round(start, 2),
                    "end_time": round(end, 2),
                }
            )
        )

    clamped = [_clamp_segment(seg, activity_map.duration_sec) for seg in aligned]
    return result.model_copy(update={"segments": clamped})
