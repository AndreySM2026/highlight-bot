from __future__ import annotations

from pathlib import Path

from pydantic import Field
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

    host: str = "0.0.0.0"
    port: int = 8080

    database_path: Path = Path("data/bot.db")
    temp_dir: Path = Path("data/temp")

    max_video_duration_sec: int = 1200
    max_clips: int = 10
    max_videos_per_day: int = 10
    min_clip_sec: int = 15
    max_clip_sec: int = 60

    target_width: int = 1080
    target_height: int = 1920

    activity_window_sec: int = Field(default=20, description="Размер окна карты активности")

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def full_webhook_url(self) -> str:
        base = self.webhook_url.rstrip("/")
        return f"{base}{self.webhook_path}"


settings = Settings()
