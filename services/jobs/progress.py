from __future__ import annotations

from config.constants import PROGRESS_STAGES


def stage_to_percent(stage: str, ratio: float = 1.0) -> int:
    start, end = PROGRESS_STAGES.get(stage, (0, 100))
    ratio = max(0.0, min(1.0, ratio))
    return int(start + (end - start) * ratio)


def progress_message(percent: int, text: str = "Обработка") -> str:
    bar_len = 10
    filled = int(bar_len * percent / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"{text}: {percent}%\n{bar}"
