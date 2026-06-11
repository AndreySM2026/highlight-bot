from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog

from services.video.ffmpeg_paths import get_ffmpeg_binary, get_ffprobe_binary

logger = structlog.get_logger(__name__)


class FFmpegError(Exception):
    pass


async def run_ffmpeg(args: list[str], *, label: str = "ffmpeg", timeout: float = 900) -> str:
    cmd = [get_ffmpeg_binary(), "-y", *args]
    logger.debug("ffmpeg_run", label=label, cmd=" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(f"{label} timed out after {timeout:.0f}s")
    output = (stderr or stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"{label} failed: {output[-1000:]}")
    return output


async def run_ffprobe(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        get_ffprobe_binary(),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {(stderr or b'').decode()}")
    return json.loads(stdout.decode())
