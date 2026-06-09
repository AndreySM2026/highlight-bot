from __future__ import annotations

import json
import re

from services.timeweb.exceptions import TimewebParseError


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
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise TimewebParseError(f"Invalid JSON in model response: {exc}") from exc

    raise TimewebParseError("No JSON object found in model response")
