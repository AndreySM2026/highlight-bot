from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from config.settings import settings
from services.highlights.utterances import normalize_speech_text
from services.highlights.schemas import HighlightSegment, TranscriptSegment
from services.video.aspect import TARGET_HEIGHT, TARGET_WIDTH
from services.video.ffmpeg import run_ffmpeg
from services.video.reframe import load_split_ranges

logger = structlog.get_logger(__name__)


def _ass_header() -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{settings.subtitles_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,2,{settings.subtitles_alignment},60,60,{settings.subtitles_margin_v},1
Style: Seam,DejaVu Sans,{settings.subtitles_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,2,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def load_transcript(job_dir: Path) -> list[TranscriptSegment]:
    path = job_dir / "transcript.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [TranscriptSegment.model_validate(item) for item in raw]
    except Exception as exc:
        logger.warning("transcript_load_failed", error=str(exc))
        return []


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole = int(secs)
    centis = int(round((secs - whole) * 100))
    if centis == 100:
        whole += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def _escape_ass_line(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def wrap_subtitle_pages(text: str, *, max_chars: int | None = None) -> list[list[str]]:
    """
    Разбивает фразу на «страницы» по max_lines строк — без потери слов.
    Слова распределяются по страницам поровну, чтобы последняя не оказалась одним словом.
    """
    lines = _wrap_all_lines(text, max_chars=max_chars)
    per_page = max(1, settings.subtitles_max_lines)
    greedy = [lines[i : i + per_page] for i in range(0, len(lines), per_page)]
    if len(greedy) < 2:
        return greedy

    words = normalize_speech_text(text).split()
    target = len(" ".join(words)) / len(greedy)
    balanced: list[list[str]] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        pages_left = len(greedy) - len(balanced) - 1
        if pages_left > 0 and len(" ".join(current)) >= target:
            balanced.append(_wrap_all_lines(" ".join(current), max_chars=max_chars))
            current = []
    if current:
        balanced.append(_wrap_all_lines(" ".join(current), max_chars=max_chars))
    if any(len(page) > per_page for page in balanced):
        return greedy  # ровно поделить не вышло — оставляем как было, слова не теряем
    return balanced


def _merge_tiny_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Whisper иногда отдаёт отдельный сегмент из одного слова («пользу.») — клеим к предыдущему."""
    merged: list[TranscriptSegment] = []
    for seg in segments:
        words = seg.text.split()
        if (
            merged
            and (len(words) <= 1 or len(seg.text) < 8)
            and seg.start - merged[-1].end <= 0.6
        ):
            prev = merged[-1]
            merged[-1] = prev.model_copy(update={"end": seg.end, "text": f"{prev.text} {seg.text}"})
            continue
        merged.append(seg)
    return merged


def _wrap_all_lines(text: str, *, max_chars: int | None = None) -> list[str]:
    text = normalize_speech_text(text)
    limit = max_chars or settings.subtitles_max_chars_per_line
    if not text:
        return []
    words = re.split(r"\s+", text)

    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        extra = len(word) if not current else len(word) + 1
        if current and length + extra > limit:
            lines.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += extra
    if current:
        lines.append(" ".join(current))
    return lines


def _format_ass_text(lines: list[str]) -> str:
    return "\\N".join(_escape_ass_line(line) for line in lines)


def build_ass_for_clip(
    transcript: list[TranscriptSegment],
    *,
    clip_start: float,
    clip_end: float,
    output_path: Path,
    split_ranges: list[tuple[float, float]] | None = None,
) -> bool:
    clip_duration = max(0.1, clip_end - clip_start)
    dialogues: list[str] = []
    split_ranges = split_ranges or []

    def style_for(start: float, end: float) -> str:
        # На разделённом экране внизу лицо второго спикера — текст ставим на шов посередине
        mid = (start + end) / 2
        return "Seam" if any(a <= mid < b for a, b in split_ranges) else "Default"

    edges = sorted({edge for rng in split_ranges for edge in rng})

    def pieces(start: float, end: float) -> list[tuple[float, float]]:
        """Режем показ фразы по сменам раскладки, чтобы текст не остался на лице после склейки."""
        cuts = [start, *(e for e in edges if start < e < end), end]
        return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= 0.05]

    in_clip = [s for s in transcript if s.end > clip_start and s.start < clip_end]
    for seg in _merge_tiny_segments(sorted(in_clip, key=lambda s: s.start)):
        rel_start = max(0.0, seg.start - clip_start)
        rel_end = min(clip_duration, seg.end - clip_start)
        if rel_end - rel_start < 0.25:
            continue
        pages = wrap_subtitle_pages(seg.text)
        if not pages:
            continue
        # Длинная фраза идёт несколькими экранами подряд; время делим пропорционально числу символов
        total_chars = sum(len(" ".join(page)) for page in pages)
        page_start = rel_start
        for page in pages:
            share = len(" ".join(page)) / total_chars if total_chars else 1 / len(pages)
            page_end = min(rel_end, page_start + (rel_end - rel_start) * share)
            for piece_start, piece_end in pieces(page_start, page_end):
                dialogues.append(
                    "Dialogue: 0,"
                    f"{_ass_timestamp(piece_start)},"
                    f"{_ass_timestamp(piece_end)},"
                    f"{style_for(piece_start, piece_end)},,0,0,0,,{_format_ass_text(page)}"
                )
            page_start = page_end

    if not dialogues:
        return False

    output_path.write_text(_ass_header() + "\n".join(dialogues) + "\n", encoding="utf-8-sig")
    return True


async def burn_subtitles(video_path: Path, ass_path: Path, *, clip_duration: float) -> Path:
    timeout = min(600.0, max(120.0, clip_duration * 6.0 + 60.0))
    temp_path = video_path.with_name(f"{video_path.stem}_subbed{video_path.suffix}")
    ass_filter = f"ass={ass_path}"
    await run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vf",
            ass_filter,
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            str(temp_path),
        ],
        label="burn_subtitles",
        timeout=timeout,
    )
    if temp_path.exists() and temp_path.stat().st_size > 0:
        video_path.unlink(missing_ok=True)
        temp_path.rename(video_path)
    return video_path


async def apply_subtitles_to_clip(
    video_path: Path,
    transcript: list[TranscriptSegment],
    segment: HighlightSegment,
) -> Path:
    ass_path = video_path.with_suffix(".ass")
    clip_duration = max(0.1, segment.end_time - segment.start_time)
    try:
        if not build_ass_for_clip(
            transcript,
            clip_start=segment.start_time,
            clip_end=segment.end_time,
            output_path=ass_path,
            split_ranges=load_split_ranges(video_path),
        ):
            logger.warning("subtitles_skipped_empty", path=str(video_path))
            return video_path
        await burn_subtitles(video_path, ass_path, clip_duration=clip_duration)
        logger.info("subtitles_burned", path=str(video_path))
        return video_path
    finally:
        ass_path.unlink(missing_ok=True)
