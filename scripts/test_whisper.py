#!/usr/bin/env python3
"""Локальная проверка Whisper на WAV/MP4 (нужен ffmpeg в PATH или bin/)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.highlights.transcript import attach_transcript_to_blocks
from services.highlights.speech_blocks import build_speech_blocks
from services.highlights.schemas import ActivityMap, SilentRange
from services.video.audio import extract_audio
from services.video.transcribe import transcribe_audio


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_whisper.py path/to/video.mp4")
        sys.exit(1)

    video = Path(sys.argv[1])
    if not video.exists():
        print(f"File not found: {video}")
        sys.exit(1)

    audio = await extract_audio(video)
    duration = 300.0
    segments = await transcribe_audio(audio, duration_sec=duration)
    audio.unlink(missing_ok=True)

    activity_map = ActivityMap(
        duration_sec=duration,
        windows=[],
        silent_ranges=[SilentRange(start=0, end=1)],
        transcript_segments=segments,
    )
    blocks = attach_transcript_to_blocks(build_speech_blocks(activity_map), segments)

    print(json.dumps([b.model_dump() for b in blocks[:5]], ensure_ascii=False, indent=2))
    print(f"\nВсего сегментов Whisper: {len(segments)}")


if __name__ == "__main__":
    asyncio.run(main())
