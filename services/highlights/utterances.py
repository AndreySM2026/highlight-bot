from __future__ import annotations

import re

from config.settings import settings
from services.highlights.schemas import (
    ActivityMap,
    HighlightResult,
    HighlightSegment,
    SpeechBlock,
    TranscriptSegment,
)

_SENTENCE_END = re.compile(r'[.!?…]["\')\]]*\s*$')
_BAD_START = re.compile(
    r"^(и|а|но|что|который|которая|которые|потому|поэтому|также|тоже|ещё|еще|"
    r"этого|этом|этим|того|тем|так|вот|ну|значит|короче)\b",
    re.IGNORECASE,
)
_TRAILING_SLASH = re.compile(r"[/\\]+\s*$")
_MID_SLASH = re.compile(r"\s+[/\\]\s+")


def normalize_speech_text(text: str) -> str:
    """Чистка текста Whisper для субтитров и анализа."""
    cleaned = text.strip()
    cleaned = _MID_SLASH.sub(" ", cleaned)
    cleaned = _TRAILING_SLASH.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def is_complete_thought(text: str, *, require_sentence_end: bool = False) -> bool:
    text = normalize_speech_text(text)
    if len(text.split()) < 8:
        return False
    if require_sentence_end and not _SENTENCE_END.search(text):
        return False
    if _BAD_START.search(text):
        return False
    return True


def text_for_range(transcript: list[TranscriptSegment], start: float, end: float) -> str:
    parts: list[str] = []
    for seg in transcript:
        if seg.end <= start or seg.start >= end:
            continue
        parts.append(normalize_speech_text(seg.text))
    return normalize_speech_text(" ".join(parts))


def build_utterance_blocks(transcript: list[TranscriptSegment]) -> list[SpeechBlock]:
    """Группирует сегменты Whisper в законченные фразы."""
    if not transcript:
        return []

    sorted_segs = sorted(transcript, key=lambda s: s.start)
    raw_chunks: list[tuple[float, float, str]] = []

    buf_start = sorted_segs[0].start
    buf_end = sorted_segs[0].end
    buf_parts = [normalize_speech_text(sorted_segs[0].text)]

    def flush_buffer() -> None:
        nonlocal buf_start, buf_end, buf_parts
        text = normalize_speech_text(" ".join(buf_parts))
        if text and (buf_end - buf_start) >= settings.utterance_min_sec:
            raw_chunks.append((buf_start, buf_end, text))
        buf_parts = []

    for seg in sorted_segs[1:]:
        gap = seg.start - buf_end
        text = normalize_speech_text(seg.text)
        prev_text = normalize_speech_text(" ".join(buf_parts))
        sentence_closed = bool(_SENTENCE_END.search(prev_text))

        if gap >= settings.utterance_pause_sec or (
            sentence_closed and len(prev_text.split()) >= 5
        ):
            flush_buffer()
            buf_start = seg.start
            buf_parts = [text]
        else:
            buf_parts.append(text)
        buf_end = seg.end

    flush_buffer()

    blocks: list[SpeechBlock] = []
    for idx, (start, end, text) in enumerate(raw_chunks):
        blocks.append(
            SpeechBlock(
                id=idx,
                start=round(start, 2),
                end=round(end, 2),
                duration=round(end - start, 2),
                text=text,
            )
        )
    return blocks


def fit_blocks_to_max_duration(blocks: list[SpeechBlock]) -> list[SpeechBlock]:
    if not blocks:
        return blocks
    total = blocks[-1].end - blocks[0].start
    if total <= settings.max_clip_sec:
        return blocks
    fitted: list[SpeechBlock] = []
    for block in blocks:
        candidate = fitted + [block]
        span = candidate[-1].end - candidate[0].start
        if span <= settings.max_clip_sec:
            fitted = candidate
        else:
            break
    if not fitted:
        return []
    if fitted[-1].end - fitted[0].start > settings.max_clip_sec:
        return []
    return fitted


def segment_from_utterance_blocks(
    blocks: list[SpeechBlock],
    *,
    title: str,
    reason: str,
    score: float,
) -> HighlightSegment | None:
    if not blocks:
        return None
    blocks = sorted(blocks, key=lambda b: b.id)
    blocks = fit_blocks_to_max_duration(blocks)
    if not blocks:
        return None
    start = blocks[0].start
    end = blocks[-1].end
    if end - start < settings.min_clip_sec:
        return None
    if end - start > settings.max_clip_sec + 0.5:
        return None
    return HighlightSegment(
        start_time=start,
        end_time=round(end, 2),
        score=score,
        title=title,
        reason=reason,
    )


def _block_indices_for_segment(segment: HighlightSegment, blocks: list[SpeechBlock]) -> list[int]:
    indices = [
        i
        for i, b in enumerate(blocks)
        if b.end > segment.start_time - 0.2 and b.start < segment.end_time + 0.2
    ]
    if indices:
        return indices

    anchor = min(range(len(blocks)), key=lambda i: abs(blocks[i].start - segment.start_time))
    return [anchor]


def expand_segment_to_utterances(
    segment: HighlightSegment,
    blocks: list[SpeechBlock],
) -> HighlightSegment | None:
    if not blocks:
        return segment

    indices = _block_indices_for_segment(segment, blocks)
    start_i = indices[0]
    end_i = indices[-1]

    def slice_text(i0: int, i1: int) -> str:
        return " ".join(b.text for b in blocks[i0 : i1 + 1] if b.text)

    text = slice_text(start_i, end_i)
    while not is_complete_thought(text) and start_i > 0:
        start_i -= 1
        text = slice_text(start_i, end_i)
        if blocks[end_i].end - blocks[start_i].start > settings.max_clip_sec:
            start_i += 1
            break

    while not is_complete_thought(text) and end_i < len(blocks) - 1:
        if blocks[end_i + 1].end - blocks[start_i].start > settings.max_clip_sec:
            break
        end_i += 1
        text = slice_text(start_i, end_i)

    chosen = fit_blocks_to_max_duration(blocks[start_i : end_i + 1])
    if not chosen:
        return None

    text = " ".join(b.text for b in chosen if b.text)
    if text and not is_complete_thought(text):
        return None

    return segment.model_copy(
        update={
            "start_time": chosen[0].start,
            "end_time": chosen[-1].end,
        }
    )


def refine_highlight_result(
    result: HighlightResult,
    activity_map: ActivityMap,
    blocks: list[SpeechBlock],
) -> HighlightResult:
    if not blocks:
        return result

    refined: list[HighlightSegment] = []
    for segment in result.segments:
        expanded = expand_segment_to_utterances(segment, blocks)
        if not expanded:
            continue
        duration = expanded.end_time - expanded.start_time
        if duration < settings.min_clip_sec or duration > settings.max_clip_sec + 0.5:
            continue
        if activity_map.transcript_segments:
            text = text_for_range(
                activity_map.transcript_segments,
                expanded.start_time,
                expanded.end_time,
            )
            if text and not is_complete_thought(text):
                continue
        refined.append(expanded)

    if not refined:
        return result

    return result.model_copy(update={"segments": refined})
