from __future__ import annotations

import aiohttp
import structlog

from config.constants import TIMEWEB_API_BASE
from config.settings import settings
from services.timeweb.exceptions import TimewebAPIError

logger = structlog.get_logger(__name__)


class TimewebClient:
    def __init__(self) -> None:
        self._base_url = TIMEWEB_API_BASE
        self._token = settings.timeweb_api_token
        self._agent_id = settings.timeweb_agent_id

    async def call_agent(self, message: str) -> str:
        url = f"{self._base_url}/cloud-ai/agents/{self._agent_id}/call"
        headers = {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
        }
        payload = {"message": message}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=120) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error("timeweb_api_error", status=resp.status, body=body[:500])
                    raise TimewebAPIError(resp.status, body)

                data = await resp.json()
                message_text = data.get("message", "")
                if isinstance(message_text, dict):
                    message_text = message_text.get("content", str(message_text))
                return str(message_text).strip()
