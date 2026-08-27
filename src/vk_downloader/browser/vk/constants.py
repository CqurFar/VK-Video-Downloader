"""VK-специфичные паттерны URL/CDN/сегментов.

Выделены из ``browser.media_browser`` чтобы изолировать знание о структуре
VK (хосты CDN, формы сегментов) от оркестрации браузера.
"""

from __future__ import annotations

import re

# Хосты CDN VK: классический vkvdNN.okcdn.ru и новые зеркала *.vkuser.net
# (например vk6-15.vkuser.net). Превью-хосты (iv./api.okcdn) не подходят.
CDN_PATTERN = re.compile(
    r"^(?:vkvd\d+\.okcdn\.ru|(?:[\w-]+\.)?vkuser\.net)$",
    re.I,
)
MPD_URL_PATTERN = re.compile(r"(?:\.mpd(?:$|[?#])|/manifest(?:[/?#]|$)|/mpd(?:[/?#]|$))", re.I)
VIDEO_SEGMENT_PATTERN = re.compile(r"(?:/fn/)?track\.v\.m4s(?:$|[?#])", re.I)
AUDIO_SEGMENT_PATTERN = re.compile(r"(?:/fn/)?track\.a\.m4s(?:$|[?#])", re.I)
VIDEO_MEDIA_PATTERN = re.compile(r"/fn/s\d+\.v\.m4s(?:$|[?#])", re.I)
AUDIO_MEDIA_PATTERN = re.compile(r"/fn/s\d+\.a\.m4s(?:$|[?#])", re.I)
