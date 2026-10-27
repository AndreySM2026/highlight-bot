from __future__ import annotations

class TimewebError(Exception):
    """Базовая ошибка клиента Timeweb."""


class TimewebAPIError(TimewebError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Timeweb API error {status}: {body}")


class TimewebQuotaError(TimewebAPIError):
    """402 token_limit_exceeded: у AI-агента закончился лимит токенов — повторять бессмысленно."""


class TimewebParseError(TimewebError):
    """Ответ модели не удалось распарсить как JSON."""
