from __future__ import annotations

import json
import re

import json_repair
import structlog

from services.timeweb.exceptions import TimewebParseError

logger = structlog.get_logger(__name__)


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    candidate = match.group() if match else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Модель часто ставит "двойные кавычки" внутри строк («Песня "Матушка"…») или теряет запятые
    repaired = json_repair.loads(candidate)
    if isinstance(repaired, dict) and repaired:
        logger.info("model_json_repaired", head=text[:200])
        return repaired

    raise TimewebParseError(f"No valid JSON in model response; head: {text[:300]!r}")
