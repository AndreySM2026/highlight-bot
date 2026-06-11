from __future__ import annotations

import re
from pathlib import Path

from config.settings import settings
from services.highlights.schemas import ActivityMap, ActivityWindow, SilentRange
from services.video.ffmpeg import run_ffmpeg


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


async def build_activity_map(video_path: Path, duration_sec: float) -> ActivityMap:
    audio_path = video_path.with_suffix(".wav")
    await run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ],
        label="extract_audio",
    )

    silence_out = await run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-32dB:d=0.35",
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

    window_size = settings.activity_window_sec
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

    if audio_path.exists():
        audio_path.unlink()

    return ActivityMap(
        duration_sec=duration_sec,
        windows=windows,
        silent_ranges=[
            SilentRange(start=round(s, 2), end=round(e, 2)) for s, e in silent_ranges
        ],
    )
