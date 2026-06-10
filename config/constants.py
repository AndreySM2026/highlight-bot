from __future__ import annotations

PROGRESS_STAGES = {
    "downloading": (0, 10),
    "normalizing": (10, 25),
    "metadata": (25, 45),
    "analyzing": (45, 70),
    "waiting_choice": (70, 70),
    "rendering": (70, 95),
    "sending": (95, 100),
}

ALLOWED_VIDEO_MIME = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/mpeg",
}

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mpeg", ".mpg"}

TIMEWEB_API_BASE = "https://api.timeweb.cloud/api/v1"

# Лимит Bot API на скачивание файлов (без Local Bot API server)
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
