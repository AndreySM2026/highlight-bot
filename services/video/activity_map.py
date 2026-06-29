from __future__ import annotations

import re
from pathlib import Path

import structlog

from config.settings import settings
from services.highlights.schemas import ActivityMap, ActivityWindow, SilentRange
from services.video.audio import extract_audio
from services.video.ffmpeg import run_ffmpeg
from services.video.long_video import activity_window_sec, is_long_video


logger = structlog.get_logger(__name__)


def _parse_silence(stderr: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]
    silent_ranges: list[tuple[float, float]] = []
    for idx, start in enumerate(starts):
        end = ends[idx] if idx < len(ends) else start
        silent_ranges.append((start, end))
    return silent_ranges


def _parse_volume(stderr: str) -> float:
    match = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", stderr)
    if match:
        return float(match.group(1))
    return -40.0


def _parse_scene_changes(stderr: str, start: float, end: float) -> int:
    timestamps = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", stderr)]
    return sum(1 for ts in timestamps if start <= ts < end)


def _speech_ratio_for_window(start: float, end: float, silent_ranges: list[tuple[float, float]]) -> float:
    window_len = max(end - start, 0.001)
    silent = 0.0
    for s_start, s_end in silent_ranges:
        overlap_start = max(start, s_start)
        overlap_end = min(end, s_end)
        if overlap_end > overlap_start:
            silent += overlap_end - overlap_start
    return max(0.0, min(1.0, 1.0 - silent / window_len))


async def build_activity_map(
    video_path: Path,
    duration_sec: float,
    *,
    audio_path: Path | None = None,
) -> tuple[ActivityMap, Path]:
    """Карта активности + путь к WAV (удалить после Whisper)."""
    owned_audio = audio_path is None
    if audio_path is None:
        audio_path = await extract_audio(video_path)

    silence_out = await run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-35dB:d=0.5",
            "-f",
            "null",
            "-",
        ],
        label="silence_detect",
    )
    volume_out = await run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        label="volume_detect",
    )
    if duration_sec >= settings.skip_scene_detect_sec:
        logger.info("scene_detect_skipped", duration_sec=round(duration_sec, 1))
        scene_out = ""
    else:
        scene_out = await run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vf",
                "select='gt(scene,0.35)',showinfo",
                "-f",
                "null",
                "-",
            ],
            label="scene_detect",
        )

    silent_ranges = _parse_silence(silence_out)
    global_volume = _parse_volume(volume_out)

    window_size = activity_window_sec(duration_sec)
    if is_long_video(duration_sec):
        logger.info("long_video_analysis_mode", duration_sec=round(duration_sec, 1), window_sec=window_size)
    windows: list[ActivityWindow] = []
    start = 0.0
    while start < duration_sec:
        end = min(start + window_size, duration_sec)
        speech_ratio = _speech_ratio_for_window(start, end, silent_ranges)
        scene_changes = _parse_scene_changes(scene_out, start, end)
        windows.append(
            ActivityWindow(
                start=round(start, 2),
                end=round(end, 2),
                avg_volume_db=round(global_volume, 2),
                speech_ratio=round(speech_ratio, 2),
                scene_changes=scene_changes,
                is_silent=speech_ratio < 0.1,
            )
        )
        start += window_size

    activity_map = ActivityMap(
        duration_sec=duration_sec,
        windows=windows,
        silent_ranges=[
            SilentRange(start=round(s, 2), end=round(e, 2)) for s, e in silent_ranges
        ],
    )
    if owned_audio and not settings.whisper_enabled and audio_path.exists():
        audio_path.unlink()
    return activity_map, audio_path
