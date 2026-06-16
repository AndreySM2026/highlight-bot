from __future__ import annotations

from pathlib import Path

from services.video.ffmpeg import run_ffmpeg


async def extract_audio(video_path: Path, *, sample_rate: int = 16000) -> Path:
    """Моно WAV 16 kHz — подходит для Whisper и silencedetect."""
    audio_path = video_path.with_suffix(".wav")
    await run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            str(audio_path),
        ],
        label="extract_audio",
    )
    return audio_path
