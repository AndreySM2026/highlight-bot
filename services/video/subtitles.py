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

logger = structlog.get_logger(__name__)

_ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{settings.subtitles_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,3,1,2,48,48,140,1

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


def _escape_ass(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def wrap_subtitle_text(text: str, *, max_chars: int | None = None) -> str:
    text = normalize_speech_text(text)
    limit = max_chars or settings.subtitles_max_chars_per_line
    if not text:
        return ""
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
    return "\\N".join(lines[: settings.subtitles_max_lines])


def build_ass_for_clip(
    transcript: list[TranscriptSegment],
    *,
    clip_start: float,
    clip_end: float,
    output_path: Path,
) -> bool:
    clip_duration = max(0.1, clip_end - clip_start)
    dialogues: list[str] = []

    for seg in transcript:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        rel_start = max(0.0, seg.start - clip_start)
        rel_end = min(clip_duration, seg.end - clip_start)
        if rel_end - rel_start < 0.25:
            continue
        text = wrap_subtitle_text(seg.text)
        if not text:
            continue
        dialogues.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(rel_start)},"
            f"{_ass_timestamp(rel_end)},"
            f"Default,,0,0,0,,{_escape_ass(text)}"
        )

    if not dialogues:
        return False

    output_path.write_text(_ASS_HEADER + "\n".join(dialogues) + "\n", encoding="utf-8-sig")
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
        ):
            logger.warning("subtitles_skipped_empty", path=str(video_path))
            return video_path
        await burn_subtitles(video_path, ass_path, clip_duration=clip_duration)
        logger.info("subtitles_burned", path=str(video_path))
        return video_path
    finally:
        ass_path.unlink(missing_ok=True)

