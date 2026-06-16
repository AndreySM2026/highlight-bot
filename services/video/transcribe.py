from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import structlog
from faster_whisper import WhisperModel

from config.settings import settings
from services.highlights.schemas import TranscriptSegment

logger = structlog.get_logger(__name__)

_model: WhisperModel | None = None
_model_lock = threading.Lock()


def _load_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            logger.info(
                "whisper_model_load",
                model=settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            _model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
        return _model


def _transcribe_sync(audio_path: Path) -> list[TranscriptSegment]:
    model = _load_model()
    language = settings.whisper_language.strip() or None
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    segments: list[TranscriptSegment] = []
    for item in segments_iter:
        text = item.text.strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=round(item.start, 2),
                end=round(item.end, 2),
                text=text,
            )
        )
    logger.info(
        "whisper_done",
        path=str(audio_path),
        segments=len(segments),
        language=getattr(info, "language", None),
    )
    return segments


def _transcribe_timeout(duration_sec: float) -> float:
    return min(900.0, max(120.0, duration_sec * 2.0))


async def transcribe_audio(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    if not settings.whisper_enabled:
        return []
    timeout = _transcribe_timeout(duration_sec)
    return await asyncio.wait_for(
        asyncio.to_thread(_transcribe_sync, audio_path),
        timeout=timeout,
    )
