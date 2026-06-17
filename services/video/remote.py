from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from config.settings import settings

Platform = Literal["rutube", "vk"]

RUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/[\w-]+",
    re.IGNORECASE,
)
RUTUBE_LOOSE_RE = re.compile(
    r"(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/[\w-]+",
    re.IGNORECASE,
)

# vk.com/video-123_456, vk.com/video123_456, vk.ru, vkvideo.ru, video_ext.php
VK_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:vk\.com|vk\.ru|vkvideo\.ru)/"
    r"(?:video[\w.-]*?-?\d+_\d+|video_ext\.php\?[^#\s]+|clip[\w.-]*?-?\d+_\d+)",
    re.IGNORECASE,
)
VK_LOOSE_RE = re.compile(
    r"(?:[\w.-]+\.)?(?:vk\.com|vk\.ru|vkvideo\.ru)/"
    r"(?:video[\w.-]*?-?\d+_\d+|video_ext\.php\?[^#\s]+|clip[\w.-]*?-?\d+_\d+)",
    re.IGNORECASE,
)

PLATFORM_LABELS = {"rutube": "Rutube", "vk": "VK"}


@dataclass(frozen=True)
class RemoteVideoLink:
    platform: Platform
    url: str

    @property
    def label(self) -> str:
        return PLATFORM_LABELS[self.platform]


def _normalize_rutube_url(url: str) -> str:
    return url.rstrip("/")


def extract_rutube_url(text: str) -> str | None:
    cleaned = text.strip()
    match = RUTUBE_URL_RE.search(cleaned)
    if match:
        return _normalize_rutube_url(match.group(0))
    loose = RUTUBE_LOOSE_RE.search(cleaned)
    if loose:
        return _normalize_rutube_url(f"https://{loose.group(0)}")
    return None


def extract_vk_url(text: str) -> str | None:
    cleaned = text.strip()
    match = VK_URL_RE.search(cleaned)
    if match:
        return match.group(0).rstrip()
    loose = VK_LOOSE_RE.search(cleaned)
    if loose:
        url = loose.group(0)
        return url if url.startswith("http") else f"https://{url}"
    return None


def parse_video_link(text: str) -> RemoteVideoLink | None:
    rutube = extract_rutube_url(text)
    if rutube and settings.rutube_enabled:
        return RemoteVideoLink(platform="rutube", url=rutube)

    vk = extract_vk_url(text)
    if vk and settings.vk_enabled:
        return RemoteVideoLink(platform="vk", url=vk)

    if rutube and not settings.rutube_enabled:
        return None
    if vk and not settings.vk_enabled:
        return None
    return None


def disabled_platform_hint(text: str) -> str | None:
    if extract_rutube_url(text) and not settings.rutube_enabled:
        return "Загрузка с Rutube временно отключена."
    if extract_vk_url(text) and not settings.vk_enabled:
        return "Загрузка с VK временно отключена."
    return None
