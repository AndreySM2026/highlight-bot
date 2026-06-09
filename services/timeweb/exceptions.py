from __future__ import annotations

class TimewebError(Exception):
    """Базовая ошибка клиента Timeweb."""


class TimewebAPIError(TimewebError):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Timeweb API error {status}: {body}")


class TimewebParseError(TimewebError):
    """Ответ модели не удалось распарсить как JSON."""
