from __future__ import annotations

from pydantic import BaseModel, Field


class ActivityWindow(BaseModel):
    start: float
    end: float
    avg_volume_db: float = -40.0
    speech_ratio: float = 0.0
    scene_changes: int = 0
    is_silent: bool = False


class SilentRange(BaseModel):
    start: float
    end: float


class SpeechBlock(BaseModel):
    """Непрерывный фрагмент речи между паузами — естественная граница мысли."""

    id: int
    start: float
    end: float
    duration: float


class ActivityMap(BaseModel):
    duration_sec: float
    windows: list[ActivityWindow]
    silent_ranges: list[SilentRange] = Field(default_factory=list)


class VideoContext(BaseModel):
    title: str = ""
    description: str = ""


class HighlightSegment(BaseModel):
    start_time: float
    end_time: float
    score: float = Field(ge=0.0, le=1.0)
    title: str = "Хайлайт"
    reason: str = ""


class HighlightResult(BaseModel):
    recommended_clip_count: int = Field(ge=1, le=10)
    segments: list[HighlightSegment]
    source: str = "qwen"
    video_theme: str = ""
