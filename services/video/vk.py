from __future__ import annotations

from pathlib import Path

from services.highlights.schemas import VideoContext
from services.video.remote import extract_vk_url
from services.video.ytdlp_common import download_remote_video, fetch_remote_metadata

__all__ = ["extract_vk_url", "fetch_vk_metadata", "download_vk_video"]


async def fetch_vk_metadata(url: str) -> VideoContext:
    return await fetch_remote_metadata(url)


async def download_vk_video(url: str, destination: Path) -> Path:
    return await download_remote_video(url, destination, platform="VK")
