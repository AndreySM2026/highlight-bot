from __future__ import annotations

from pydantic import BaseModel, Field


class ActivityWindow(BaseModel):
    start: float
    end: float
    avg_volume_db: float = -40.0
    speech_ratio: float = 0.0
    scene_changes: int = 0
    is_silent: bool = False


class ActivityMap(BaseModel):
    duration_sec: float
    windows: list[ActivityWindow]


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
