from __future__ import annotations

from config.settings import settings
from services.highlights.merge import finalize_highlight_result
from services.highlights.schemas import ActivityMap, HighlightResult, HighlightSegment


def _window_score(window) -> float:
    volume = max(0.0, min(1.0, (window.avg_volume_db + 50) / 30))
    speech = max(0.0, min(1.0, window.speech_ratio))
    scenes = max(0.0, min(1.0, window.scene_changes / 3))
    silence_penalty = 0.2 if window.is_silent else 0.0
    return max(0.0, min(1.0, 0.4 * volume + 0.35 * speech + 0.25 * scenes - silence_penalty))


def detect_highlights_heuristic(activity_map: ActivityMap) -> HighlightResult:
    if not activity_map.windows:
        return HighlightResult(
            recommended_clip_count=1,
            segments=[
                HighlightSegment(
                    start_time=0.0,
                    end_time=min(settings.max_clip_sec, activity_map.duration_sec),
                    score=0.5,
                    title="Начало видео",
                    reason="Эвристика: нет данных активности",
                )
            ],
            source="heuristic",
        )

    ranked = sorted(activity_map.windows, key=_window_score, reverse=True)
    segments: list[HighlightSegment] = []

    for idx, window in enumerate(ranked[: settings.max_clips]):
        start = window.start
        end = min(window.end, start + settings.max_clip_sec)
        if end - start < settings.min_clip_sec:
            end = min(start + settings.min_clip_sec, activity_map.duration_sec)
        segments.append(
            HighlightSegment(
                start_time=start,
                end_time=end,
                score=round(_window_score(window), 2),
                title=f"Момент {idx + 1}",
                reason="Эвристика: громкость, речь и смены сцен",
            )
        )

    raw = HighlightResult(
        recommended_clip_count=min(max(3, len(segments) // 2), len(segments)),
        segments=segments,
        source="heuristic",
    )
    return finalize_highlight_result(raw, activity_map)
