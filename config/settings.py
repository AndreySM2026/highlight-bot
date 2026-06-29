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

    @field_validator("vk_enabled", mode="before")
    @classmethod
    def parse_vk_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    database_path: Path = Path("data/bot.db")
    temp_dir: Path = Path("data/temp")

    max_video_duration_sec: int = 3600
    max_upload_bytes: int = Field(
        default=20 * 1024 * 1024,
        description="Макс. размер файла для скачивания через Bot API (20 МБ)",
    )
    rutube_enabled: bool = True
    vk_enabled: bool = True
    max_rutube_download_bytes: int = Field(
        default=1024 * 1024 * 1024,
        description="Макс. размер скачивания по ссылке Rutube/VK (1 ГБ)",
    )
    rutube_max_height: int = Field(
        default=1080,
        description="Макс. высота видео при скачивании по ссылке Rutube/VK",
    )
    max_clips: int = 10
    max_videos_per_day: int = 10
    min_clip_sec: int = 8
    max_clip_sec: int = 90

    target_width: int = 1080
    target_height: int = 1920

    activity_window_sec: int = Field(default=20, description="Размер окна карты активности")
    activity_window_long_sec: int = Field(
        default=60,
        description="Размер окна карты активности для видео ≥ long_video_sec",
    )
    long_video_sec: int = Field(
        default=1800,
        description="Порог «длинного» видео (30 мин) — ускоренный режим анализа",
    )
    skip_scene_detect_sec: int = Field(
        default=1800,
        description="Не сканировать смены сцен на видео длиннее (экономит минуты на 30–60 мин роликах)",
    )
    highlight_prompt_max_blocks: int = Field(
        default=50,
        description="Макс. speech_blocks в промпте LLM для длинных видео",
    )
    analysis_max_height: int = Field(
        default=720,
        description="Макс. высота прокси-видео для анализа (не влияет на качество клипов)",
    )
    whisper_enabled: bool = Field(
        default=True,
        description="Распознавание речи Whisper перед выбором хайлайтов",
    )
    whisper_model: str = Field(
        default="base",
        description="Модель faster-whisper для видео < 30 мин: tiny, base, small",
    )
    whisper_long_model: str = Field(
        default="tiny",
        description="Модель Whisper для видео ≥ 30 мин (быстрее в 3–5 раз)",
    )
    whisper_beam_size: int = Field(default=3, description="beam_size Whisper для коротких видео")
    whisper_long_beam_size: int = Field(default=1, description="beam_size Whisper для длинных видео")
    whisper_cpu_threads: int = Field(
        default=0,
        description="Потоки CPU для Whisper, 0 = все ядра",
    )
    whisper_language: str = Field(
        default="ru",
        description="Язык распознавания (ISO 639-1), пусто — авто",
    )
    whisper_device: str = Field(default="cpu", description="cpu или cuda")
    whisper_compute_type: str = Field(
        default="int8",
        description="Тип вычислений CPU: int8, float32; для GPU: float16",
    )

    face_crop_enabled: bool = Field(
        default=True,
        description="Центрировать 9:16 crop по лицу (OpenCV)",
    )

    @field_validator("face_crop_enabled", mode="before")
    @classmethod
    def parse_face_crop_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    subtitles_enabled: bool = Field(
        default=True,
        description="Разрешить вшитые русские субтитры (выбор кнопкой при рендере)",
    )
    subtitles_font_size: int = Field(default=52, description="Размер шрифта субтитров")
    subtitles_max_chars_per_line: int = Field(default=34, description="Символов в строке субтитра")
    subtitles_max_lines: int = Field(default=2, description="Макс. строк субтитра")
    subtitles_alignment: int = Field(
        default=5,
        description="ASS Alignment: 2 — низ по центру, 5 — середина по центру",
    )
    subtitles_margin_v: int = Field(
        default=80,
        description="ASS MarginV: отступ от низа (2) или от центра (5), пиксели",
    )

    @field_validator("subtitles_enabled", mode="before")
    @classmethod
    def parse_subtitles_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @field_validator("whisper_enabled", mode="before")
    @classmethod
    def parse_whisper_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    speech_min_pause_sec: float = Field(
        default=0.45,
        description="Мин. длина паузы для разделения речевых блоков",
    )
    utterance_pause_sec: float = Field(
        default=0.55,
        description="Пауза Whisper для границы законченной фразы",
    )
    utterance_min_sec: float = Field(
        default=1.5,
        description="Мин. длина utterance-блока при группировке Whisper",
    )
    speech_min_block_sec: float = Field(
        default=2.0,
        description="Мин. длина блока речи",
    )
    speech_align_lookback_sec: float = Field(
        default=8.0,
        description="Насколько назад искать паузу для начала клипа",
    )
    speech_align_lookahead_sec: float = Field(
        default=5.0,
        description="Насколько вперёд искать паузу для конца клипа",
    )
    speech_align_preroll_sec: float = Field(
        default=0.2,
        description="Небольшой отступ до начала речи после паузы",
    )
    speech_align_tail_sec: float = Field(
        default=0.25,
        description="Небольшой хвост после последнего слова до паузы",
    )
    speech_align_fallback_pad_sec: float = Field(
        default=2.5,
        description="Если пауза не найдена — расширить начало на столько секунд",
    )
    speech_align_fallback_tail_sec: float = Field(
        default=1.5,
        description="Если пауза не найдена — расширить конец на столько секунд",
    )
    speech_split_min_pause_sec: float = Field(
        default=0.9,
        description="Мин. пауза внутри клипа для разделения на две мысли",
    )
    speech_split_edge_margin_sec: float = Field(
        default=2.0,
        description="Не делить по паузам у самого начала/конца клипа",
    )
    speech_split_max_passes: int = Field(
        default=2,
        description="Сколько раз пытаться разделить клипы с несколькими мыслями",
    )

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def full_webhook_url(self) -> str:
        base = self.webhook_url.rstrip("/")
        return f"{base}{self.webhook_path}"

    @property
    def max_remote_download_bytes(self) -> int:
        return self.max_rutube_download_bytes

    @property
    def remote_max_height(self) -> int:
        return self.rutube_max_height


settings = Settings()
