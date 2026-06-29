from __future__ import annotations

from config.settings import settings


def is_long_video(duration_sec: float) -> bool:
    return duration_sec >= settings.long_video_sec


def activity_window_sec(duration_sec: float) -> int:
    if is_long_video(duration_sec):
        return settings.activity_window_long_sec
    return settings.activity_window_sec


def whisper_model_for_duration(duration_sec: float) -> str:
    if is_long_video(duration_sec):
        return settings.whisper_long_model
    return settings.whisper_model


def whisper_beam_size_for_duration(duration_sec: float) -> int:
    if is_long_video(duration_sec):
        return settings.whisper_long_beam_size
    return settings.whisper_beam_size
