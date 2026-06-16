from __future__ import annotations

from services.highlights.schemas import SpeechBlock, TranscriptSegment


def attach_transcript_to_blocks(
    blocks: list[SpeechBlock],
    transcript: list[TranscriptSegment],
) -> list[SpeechBlock]:
    if not transcript:
        return blocks

    enriched: list[SpeechBlock] = []
    for block in blocks:
        parts: list[str] = []
        for seg in transcript:
            overlap_start = max(block.start, seg.start)
            overlap_end = min(block.end, seg.end)
            if overlap_end - overlap_start >= 0.15:
                parts.append(seg.text.strip())
        text = " ".join(parts).strip()
        enriched.append(block.model_copy(update={"text": text}))
    return enriched
