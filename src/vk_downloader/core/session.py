"""Immutable media session — снимок MPD + credentials + segment bases.

Отделяет эфемерное состояние браузера (подписанные URL, куки) от
оркестрации. Позволяет retry-логике понять, нужно ли рефрешить сессию.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MediaSession:
    """Снимок браузерной сессии для одного видео."""

    url: str  # исходный VK URL
    mpd_url: str
    mpd_text: str
    title: str
    user_agent: str
    cookies: tuple[dict, ...] = field(default_factory=tuple)
    video_segment_url: str | None = None
    audio_segment_url: str | None = None
    video_segment_base: str | None = None
    audio_segment_base: str | None = None
    captured_at: float = field(default_factory=time.monotonic)
    ttl: float = 300.0  # подписанные CDN URL живут ~5 мин

    def __post_init__(self) -> None:
        # Гарантируем иммутабельность: внешний list не должен мутировать сессию
        if not isinstance(self.cookies, tuple):
            object.__setattr__(self, "cookies", tuple(self.cookies))

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self.captured_at) > self.ttl

    @property
    def is_expired(self) -> bool:
        return self.is_stale

    # --- Совместимость со старым dict-кодом ---------------------------------
    def get(self, key: str, default=None):
        return getattr(self, key, default) if hasattr(self, key) else default

    def __getitem__(self, key: str):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "mpd_url": self.mpd_url,
            "mpd_text": self.mpd_text,
            "title": self.title,
            "user_agent": self.user_agent,
            "cookies": self.cookies,
            "video_segment_url": self.video_segment_url,
            "audio_segment_url": self.audio_segment_url,
            "video_segment_base": self.video_segment_base,
            "audio_segment_base": self.audio_segment_base,
            "captured_at": self.captured_at,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, url: str, data: dict, ttl: float = 300.0) -> MediaSession:
        return cls(
            url=url,
            mpd_url=data.get("mpd_url", ""),
            mpd_text=data.get("mpd_text", ""),
            title=data.get("title", ""),
            user_agent=data.get("user_agent", ""),
            cookies=tuple(data.get("cookies", [])),
            video_segment_url=data.get("video_segment_url"),
            audio_segment_url=data.get("audio_segment_url"),
            video_segment_base=data.get("video_segment_base"),
            audio_segment_base=data.get("audio_segment_base"),
            captured_at=data.get("captured_at", time.monotonic()),
            ttl=data.get("ttl", ttl),
        )

    def refresh_needed(self, exc: BaseException | None = None) -> bool:
        """Нужно ли рефрешить сессию перед ретраем.

        - сессия протухла по TTL — да;
        - ошибка похожа на 401/403/expired/signature — да;
        - иначе — нет (транзиентный CDN таймаут можно ретраить со старой сессией).
        """
        if self.is_stale:
            return True
        if exc is None:
            return False
        msg = str(exc).lower()
        return any(
            k in msg
            for k in ("401", "403", "expired", "signature", "token", "forbidden", "unauthorized")
        )
