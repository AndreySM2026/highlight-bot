from __future__ import annotations

import asyncio
import os
import threading
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
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
# Два задания из очереди могут дойти до расшифровки одновременно: модель одна, ядер 4 —
# параллельно быстрее не будет, а память удвоится. Расшифровываем строго по одному.
_transcribe_lock = threading.Lock()

_SAMPLE_RATE = 16000


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


def _iter_audio_chunks(audio_path: Path, chunk_sec: int) -> Iterator[tuple[float, np.ndarray | str]]:
    """
    Отдаёт аудио кусками (offset_sec, samples), чтобы память не росла с длиной видео.
    faster-whisper иначе грузит весь файл и строит спектрограмму целиком — на 25+ мин это OOM.
    """
    try:
        wav = wave.open(str(audio_path), "rb")
    except (wave.Error, EOFError) as exc:
        logger.warning("whisper_chunking_unavailable", error=str(exc))
        yield 0.0, str(audio_path)
        return

    with wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, _SAMPLE_RATE):
            logger.warning(
                "whisper_chunking_unsupported_format",
                channels=wav.getnchannels(),
                sample_width=wav.getsampwidth(),
                rate=wav.getframerate(),
            )
            yield 0.0, str(audio_path)
            return

        frames_per_chunk = chunk_sec * _SAMPLE_RATE
        offset = 0.0
        while True:
            raw = wav.readframes(frames_per_chunk)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            yield offset, samples
            offset += len(samples) / _SAMPLE_RATE


def _transcribe_sync(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    with _transcribe_lock:
        return _transcribe_locked(audio_path, duration_sec=duration_sec)


def _transcribe_locked(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    model_name = whisper_model_for_duration(duration_sec)
    beam_size = whisper_beam_size_for_duration(duration_sec)
    model = _load_model(model_name)
    language = settings.whisper_language.strip() or None
    chunk_sec = max(60, settings.whisper_chunk_sec)
    segments: list[TranscriptSegment] = []
    info = None
    for chunk_idx, (offset, audio) in enumerate(_iter_audio_chunks(audio_path, chunk_sec)):
        segments_iter, info = model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )
        for item in segments_iter:
            text = normalize_speech_text(item.text)
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start=round(item.start + offset, 2),
                    end=round(item.end + offset, 2),
                    text=text,
                )
            )
        logger.info(
            "whisper_chunk_done",
            chunk=chunk_idx + 1,
            offset_sec=round(offset, 1),
            segments_total=len(segments),
        )
    logger.info(
        "whisper_done",
        path=str(audio_path),
        segments=len(segments),
        language=getattr(info, "language", None),
        model=model_name,
        beam_size=beam_size,
        chunk_sec=chunk_sec,
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
