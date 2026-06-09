from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BIN = PROJECT_ROOT / "bin"


def get_ffmpeg_binary() -> str:
    return _resolve_binary("FFMPEG_PATH", "ffmpeg")


def get_ffprobe_binary() -> str:
    return _resolve_binary("FFPROBE_PATH", "ffprobe")


def _resolve_binary(env_var: str, name: str) -> str:
    custom = os.getenv(env_var)
    if custom and Path(custom).exists():
        return custom

    local = LOCAL_BIN / name
    if local.exists():
        return str(local)

    found = shutil.which(name)
    if found:
        return found

    raise RuntimeError(
        f"{name} не найден. Варианты:\n"
        f"1) Docker: docker compose up\n"
        f"2) Скачайте {name} в папку {LOCAL_BIN}/\n"
        f"3) brew install ffmpeg"
    )


def ensure_ffmpeg_available() -> None:
    get_ffmpeg_binary()
    get_ffprobe_binary()
