from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import structlog
from faster_whisper import WhisperModel

from config.settings import settings
from services.highlights.schemas import TranscriptSegment
from services.highlights.utterances import normalize_speech_text
from services.video.long_video import whisper_beam_size_for_duration, whisper_model_for_duration

logger = structlog.get_logger(__name__)

_model: WhisperModel | None = None
_model_name: str | None = None
_model_lock = threading.Lock()


def _cpu_threads() -> int:
    if settings.whisper_cpu_threads > 0:
        return settings.whisper_cpu_threads
    return max(1, os.cpu_count() or 1)


def _load_model(model_name: str) -> WhisperModel:
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != model_name:
            if _model is not None:
                logger.info("whisper_model_release", previous=_model_name)
                _model = None
            logger.info(
                "whisper_model_load",
                model=model_name,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=_cpu_threads(),
            )
            _model = WhisperModel(
                model_name,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=_cpu_threads(),
            )
            _model_name = model_name
        return _model


def _transcribe_sync(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    model_name = whisper_model_for_duration(duration_sec)
    beam_size = whisper_beam_size_for_duration(duration_sec)
    model = _load_model(model_name)
    language = settings.whisper_language.strip() or None
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    segments: list[TranscriptSegment] = []
    for item in segments_iter:
        text = normalize_speech_text(item.text)
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
        model=model_name,
        beam_size=beam_size,
        duration_sec=round(duration_sec, 1),
    )
    return segments


def _transcribe_timeout(duration_sec: float) -> float:
    if duration_sec >= settings.long_video_sec:
        return min(5400.0, max(600.0, duration_sec * 0.8))
    return min(7200.0, max(300.0, duration_sec * 1.2))


async def transcribe_audio(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    if not settings.whisper_enabled:
        return []
    timeout = _transcribe_timeout(duration_sec)
    return await asyncio.wait_for(
        asyncio.to_thread(_transcribe_sync, audio_path, duration_sec=duration_sec),
        timeout=timeout,
    )


def release_whisper_model() -> None:
    """Освобождает RAM после транскрипции — перед тяжёлым ffmpeg-рендером."""
    global _model, _model_name
    with _model_lock:
        if _model is not None:
            logger.info("whisper_model_release", model=_model_name)
            _model = None
            _model_name = None
