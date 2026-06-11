from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""
    timeweb_api_token: str = ""
    timeweb_agent_id: str = ""

    webhook_url: str = ""
    webhook_secret: str = "change_me"
    # Если api.telegram.org недоступен (РФ): socks5://host:port или http://host:port
    telegram_proxy: str = ""
    # Свой Bot API сервер (VPS за рубежом): http://your-vps:8081
    telegram_api_base_url: str = ""

    host: str = "0.0.0.0"
    port: int = 8080

    @field_validator("port", mode="before")
    @classmethod
    def parse_port(cls, value: object) -> int:
        if value is None or value == "":
            return 8080
        return int(value)

    @field_validator("host", mode="before")
    @classmethod
    def parse_host(cls, value: object) -> str:
        if not value or value == "":
            return "0.0.0.0"
        return str(value)

    @field_validator(
        "bot_token",
        "timeweb_api_token",
        "timeweb_agent_id",
        "webhook_url",
        "webhook_secret",
        "telegram_proxy",
        "telegram_api_base_url",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("webhook_url")
    @classmethod
    def normalize_webhook_url(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            return f"https://{value}"
        return value

    @field_validator("rutube_enabled", mode="before")
    @classmethod
    def parse_rutube_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    database_path: Path = Path("data/bot.db")
    temp_dir: Path = Path("data/temp")

    max_video_duration_sec: int = 1200
    max_upload_bytes: int = Field(
        default=20 * 1024 * 1024,
        description="Макс. размер файла для скачивания через Bot API (20 МБ)",
    )
    rutube_enabled: bool = True
    max_rutube_download_bytes: int = Field(
        default=1024 * 1024 * 1024,
        description="Макс. размер скачивания с Rutube (1 ГБ)",
    )
    rutube_max_height: int = Field(
        default=1080,
        description="Макс. высота видео при скачивании с Rutube",
    )
    max_clips: int = 10
    max_videos_per_day: int = 10
    min_clip_sec: int = 15
    max_clip_sec: int = 60

    target_width: int = 1080
    target_height: int = 1920

    activity_window_sec: int = Field(default=20, description="Размер окна карты активности")
    analysis_max_height: int = Field(
        default=720,
        description="Макс. высота прокси-видео для анализа (не влияет на качество клипов)",
    )

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def full_webhook_url(self) -> str:
        base = self.webhook_url.rstrip("/")
        return f"{base}{self.webhook_path}"


settings = Settings()
