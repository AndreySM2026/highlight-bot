"""
Второй проход ИИ: редакторская проверка каждого выбранного отрывка.

ИИ видит текст отрывка и по несколько фраз до и после него и решает:
- ok — мысль начинается с начала и закончена, лишнего нет;
- fix — сдвинуть границы (новые start_block/end_block из показанных фраз);
- reject — отрывок не годится (мысль не самостоятельна, оборвана, две разные мысли).
Пользователю уходят только отрывки, прошедшие проверку.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from config.settings import settings
from services.highlights.schemas import SpeechBlock
from services.timeweb.client import TimewebClient
from services.timeweb.json_parser import extract_json

logger = structlog.get_logger(__name__)

CONTEXT_BLOCKS = 4


@dataclass
class Candidate:
    first: int
    last: int
    title: str
    reason: str
    score: float


def _line(block: SpeechBlock) -> str:
    return f"[{block.id}] ({block.duration:.0f}с) {block.text or '(без расшифровки)'}"


def build_verify_prompt(candidates: list[Candidate], blocks: list[SpeechBlock]) -> str:
    parts: list[str] = []
    for idx, cand in enumerate(candidates, start=1):
        before = blocks[max(0, cand.first - CONTEXT_BLOCKS) : cand.first]
        inside = blocks[cand.first : cand.last + 1]
        after = blocks[cand.last + 1 : cand.last + 1 + CONTEXT_BLOCKS]
        span = inside[-1].end - inside[0].start
        parts.append(
            f"### Отрывок {idx}: «{cand.title}» — сейчас фразы {cand.first}–{cand.last}, {span:.0f} с\n"
            "ДО отрывка:\n" + ("\n".join(_line(b) for b in before) or "(начало выпуска)") + "\n"
            "ОТРЫВОК:\n" + "\n".join(_line(b) for b in inside) + "\n"
            "ПОСЛЕ отрывка:\n" + ("\n".join(_line(b) for b in after) or "(конец выпуска)")
        )

    return f"""Ты — выпускающий редактор коротких видео для подкаста о маркетинге «Медиамикс».
Проверь каждый отрывок: зритель увидит ТОЛЬКО его, без остального выпуска.

Отрывок годится, только если ВСЁ верно:
1. Начало — начало мысли. Первая фраза понятна без того, что было ДО: не продолжение чужой фразы,
   не ответ на вопрос, которого в отрывке нет, не «и/но/а/поэтому/вот/это/он…» в начале.
2. Конец — мысль завершена: есть вывод, ответ или пуанта. Не обрывается на полуслове или перечислении.
3. Внутри одна мысль. Если в конце уже началась следующая тема — её надо отрезать.
4. Длительность от {settings.min_clip_sec} до {settings.max_clip_sec} секунд.
5. Это интересно и полезно маркетологу само по себе.

Для каждого отрывка верни решение:
- "ok" — годится как есть;
- "fix" — годится, если сдвинуть границы: укажи новые start_block и end_block, ТОЛЬКО из номеров,
  показанных для этого отрывка (ДО / ОТРЫВОК / ПОСЛЕ);
- "reject" — не годится даже с правкой границ.
Будь строгим: сомневаешься, что мысль закончена или понятна, — "fix" или "reject".

Ответь ТОЛЬКО валидным JSON без markdown:
{{"clips": [{{"clip": 1, "verdict": "ok", "start_block": 41, "end_block": 47, "problem": "что не так (пусто, если ok)"}}]}}

{chr(10).join(parts)}
"""


def apply_verdicts(
    data: dict,
    candidates: list[Candidate],
    blocks: list[SpeechBlock],
) -> list[Candidate]:
    by_clip = {}
    for item in data.get("clips", []) or []:
        try:
            by_clip[int(item.get("clip"))] = item
        except (TypeError, ValueError):
            continue

    approved: list[Candidate] = []
    for idx, cand in enumerate(candidates, start=1):
        item = by_clip.get(idx)
        if not item:
            logger.info("verify_missing_verdict", clip=idx)
            continue  # без решения редактора не выдаём
        verdict = str(item.get("verdict", "")).lower()
        problem = str(item.get("problem", ""))[:200]
        if verdict == "reject":
            logger.info("verify_rejected", clip=idx, title=cand.title, problem=problem)
            continue

        first, last = cand.first, cand.last
        if verdict == "fix":
            try:
                first, last = int(item["start_block"]), int(item["end_block"])
            except (KeyError, TypeError, ValueError):
                logger.info("verify_bad_fix", clip=idx)
                continue
            lo = max(0, cand.first - CONTEXT_BLOCKS)
            hi = min(len(blocks) - 1, cand.last + CONTEXT_BLOCKS)
            if not (lo <= first <= last <= hi):
                logger.info("verify_fix_out_of_range", clip=idx, first=first, last=last)
                continue
        elif verdict != "ok":
            logger.info("verify_unknown_verdict", clip=idx, verdict=verdict)
            continue

        span = blocks[last].end - blocks[first].start
        if not (settings.min_clip_sec <= span <= settings.max_clip_sec + 0.5):
            logger.info("verify_bad_duration", clip=idx, span=round(span, 1))
            continue
        logger.info(
            "verify_approved",
            clip=idx,
            verdict=verdict,
            old=(cand.first, cand.last),
            new=(first, last),
            problem=problem,
        )
        approved.append(Candidate(first, last, cand.title, cand.reason, cand.score))

    # После правки границ отрывки могли наехать друг на друга — оставляем более сильный
    approved.sort(key=lambda c: c.score, reverse=True)
    result: list[Candidate] = []
    for cand in approved:
        if any(cand.first <= other.last and other.first <= cand.last for other in result):
            continue
        result.append(cand)
    return result


async def verify_candidates(
    client: TimewebClient,
    candidates: list[Candidate],
    blocks: list[SpeechBlock],
) -> list[Candidate]:
    if not candidates:
        return []
    response = await client.call_agent(build_verify_prompt(candidates, blocks))
    return apply_verdicts(extract_json(response), candidates, blocks)
