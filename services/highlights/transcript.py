from __future__ import annotations

from services.highlights.schemas import SpeechBlock, TranscriptSegment
from services.highlights.utterances import normalize_speech_text


def attach_transcript_to_blocks(
    blocks: list[SpeechBlock],
    transcript: list[TranscriptSegment],
) -> list[SpeechBlock]:
    if not transcript:
        return blocks

    enriched: list[SpeechBlock] = []
    for block in blocks:
        if block.text:
            enriched.append(block.model_copy(update={"text": normalize_speech_text(block.text)}))
            continue
        parts: list[str] = []
        for seg in transcript:
            overlap_start = max(block.start, seg.start)
            overlap_end = min(block.end, seg.end)
            if overlap_end - overlap_start >= 0.15:
                parts.append(normalize_speech_text(seg.text))
        text = normalize_speech_text(" ".join(parts))
        enriched.append(block.model_copy(update={"text": text}))
    return enriched
