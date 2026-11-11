"""
Облачная расшифровка речи: Whisper Large v3 в TimeWeb AI Gateway (0,55 ₽ за минуту аудио).

На русском заметно точнее локальной модели base: на подкасте про ИИ base писала
«общему И», «с УПЕР И», Large v3 — «общего искусственного интеллекта», «суперискусственного».
И быстрее: 5 минут аудио — ~11 с против ~50 с локально.

Звук режется на куски ~30 с по паузам, куски уходят в облако параллельно
(на длинных кусках таймкоды облака уезжают на секунды). Кусок, который облако не распознало, расшифровывается локально —
видео обрабатывается в любом случае.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import subprocess
import wave
from pathlib import Path

import aiohttp
import numpy as np
import structlog

from config.settings import settings
from services.highlights.schemas import TranscriptSegment
from services.highlights.utterances import normalize_speech_text
from services.video.ffmpeg_paths import get_ffmpeg_binary

logger = structlog.get_logger(__name__)

_SAMPLE_RATE = 16000
_ATTEMPTS = 2

# Whisper на тишине и музыке иногда «придумывает» фразы из титров видео
_HALLUCINATIONS = re.compile(
    r"(субтитр|продолжение следует|спасибо за просмотр|подписывайтесь на канал|редактор субтитров|"
    r"dimatorzok|amara\.org)",
    re.IGNORECASE,
)


def cloud_asr_enabled() -> bool:
    return bool(settings.timeweb_ai_gateway_key) and settings.asr_provider in {"auto", "timeweb"}


def _wav_bytes(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


def _mp3_bytes(samples: np.ndarray) -> bytes:
    """
    Сжимаем кусок в MP3 48 кбит/с: 5 минут ≈ 1,8 МБ вместо 9,6 МБ WAV.
    Несжатые куски шлюз TimeWeb обрывал (Connection reset / Broken pipe).
    """
    proc = subprocess.run(
        [get_ffmpeg_binary(), "-v", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1"],
        input=_wav_bytes(samples),
        capture_output=True,
        timeout=120,
        check=True,
    )
    return proc.stdout


def _segments_from_response(data: dict, offset: float) -> list[TranscriptSegment]:
    result: list[TranscriptSegment] = []
    for seg in data.get("segments") or []:
        text = normalize_speech_text(str(seg.get("text", "")))
        if not text or _HALLUCINATIONS.search(text):
            continue
        if float(seg.get("no_speech_prob", 0)) > 0.6 and float(seg.get("avg_logprob", 0)) < -1.0:
            continue
        start, end = float(seg.get("start", 0)), float(seg.get("end", 0))
        result.append(
            TranscriptSegment(
                start=round(start + offset, 2),
                end=round(max(end, start + 0.1) + offset, 2),
                text=text,
            )
        )
    return result


def _without_overlaps(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Large v3 часто тянет конец сегмента за начало следующего — обрезаем по началу следующего."""
    result: list[TranscriptSegment] = []
    for idx, seg in enumerate(segments):
        if idx + 1 < len(segments):
            next_start = segments[idx + 1].start
            if seg.end > next_start:
                seg = seg.model_copy(update={"end": round(max(next_start, seg.start + 0.1), 2)})
        result.append(seg)
    return result


async def _transcribe_chunk(session: aiohttp.ClientSession, samples: np.ndarray, offset: float) -> list[TranscriptSegment]:
    last_error: Exception | None = None
    audio = await asyncio.to_thread(_mp3_bytes, samples)
    for attempt in range(1, _ATTEMPTS + 1):
        form = aiohttp.FormData()
        form.add_field("model", settings.asr_cloud_model)
        form.add_field("response_format", "verbose_json")
        if settings.whisper_language.strip():
            form.add_field("language", settings.whisper_language.strip())
        form.add_field("file", audio, filename="chunk.mp3", content_type="audio/mpeg")
        try:
            async with session.post(
                f"{settings.timeweb_ai_gateway_url.rstrip('/')}/audio/transcriptions",
                data=form,
                headers={"Authorization": f"Bearer {settings.timeweb_ai_gateway_key}"},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
                return _segments_from_response(json.loads(body), offset)
        except Exception as exc:
            last_error = exc
            logger.warning("cloud_asr_chunk_failed", offset_sec=round(offset, 1), attempt=attempt, error=str(exc)[:300])
            await asyncio.sleep(3 * attempt)
    raise RuntimeError(str(last_error))


async def transcribe_cloud(audio_path: Path, *, duration_sec: float) -> list[TranscriptSegment]:
    """Расшифровка через Whisper Large v3 в TimeWeb; куски, не распознанные облаком, — локально."""
    from services.video.transcribe import iter_audio_chunks, transcribe_samples_locally

    # Короткие куски: внутри длинного таймкоды облака уезжают вперёд (до +4 с к концу 5 минут),
    # клип начинался позже начала мысли, субтитры отставали от голоса. На 30 с — расхождение ~0,1 с.
    chunk_sec = max(10, settings.asr_cloud_chunk_sec)
    chunks = list(iter_audio_chunks(audio_path, chunk_sec, search_sec=max(2.0, chunk_sec / 5)))
    semaphore = asyncio.Semaphore(settings.asr_cloud_parallel)
    fallbacks = 0

    async with aiohttp.ClientSession() as session:

        async def run(offset: float, samples) -> list[TranscriptSegment]:
            nonlocal fallbacks
            if isinstance(samples, str):  # формат WAV не тот — режем не смогли, весь файл локально
                return await asyncio.to_thread(transcribe_samples_locally, samples, 0.0, duration_sec)
            async with semaphore:
                try:
                    return await _transcribe_chunk(session, samples, offset)
                except Exception as exc:
                    fallbacks += 1
                    logger.warning("cloud_asr_fallback_local", offset_sec=round(offset, 1), error=str(exc)[:200])
            return await asyncio.to_thread(transcribe_samples_locally, samples, offset, duration_sec)

        parts = await asyncio.gather(*(run(offset, samples) for offset, samples in chunks))

    segments = _without_overlaps(sorted((s for part in parts for s in part), key=lambda s: s.start))
    logger.info(
        "cloud_asr_done",
        model=settings.asr_cloud_model,
        chunks=len(chunks),
        local_fallbacks=fallbacks,
        segments=len(segments),
        duration_sec=round(duration_sec, 1),
        cost_rub=round(duration_sec / 60 * settings.asr_cloud_rub_per_min, 2),
    )
    return segments
