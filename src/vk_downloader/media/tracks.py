"""Типизированная модель треков: сегменты, видео- и аудиодорожки.

Дополняет dict-представление от ``mpd_parser`` типобезопасными датаклассами.
Полный перевод consumers (quality/dash_downloader/downloader) на эту модель —
следующий шаг: пока модель строится из существующего dict через ``from_dict``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Segment:
    """Один DASH-сегмент из SegmentTimeline."""

    number: int
    time: int
    duration: int


@dataclass(slots=True)
class BaseTrack:
    """Общая часть видео/аудио трека."""

    track_id: str
    codecs: str
    bandwidth: int
    mime_type: str
    segment_template: dict
    segments: list[Segment] = field(default_factory=list)
    url: str = ""

    @classmethod
    def from_dict(cls, track: dict) -> BaseTrack:
        """Построить модель из dict, возвращаемого ``MPDParser.parse``."""
        video = (
            str(track.get("mime") or "").startswith("video") or int(track.get("height") or 0) > 0
        )
        segments = [
            Segment(
                number=int(s.get("number", 0)),
                time=int(s.get("time", 0)),
                duration=int(s.get("duration", 0)),
            )
            for s in track.get("segment_timeline") or []
        ]
        common = {
            "track_id": str(track.get("id", "")),
            "codecs": track.get("codecs", ""),
            "bandwidth": int(track.get("bandwidth") or 0),
            "mime_type": track.get("mime", ""),
            "segment_template": track.get("segment_template") or {},
            "segments": segments,
            "url": track.get("url", ""),
        }
        if video:
            return VideoTrack(
                width=int(track.get("width") or 0), height=int(track.get("height") or 0), **common
            )
        return AudioTrack(**common)


@dataclass(slots=True)
class VideoTrack(BaseTrack):
    """Видеодорожка с геометрией кадра."""

    width: int = 0
    height: int = 0


@dataclass(slots=True)
class AudioTrack(BaseTrack):
    """Аудиодорожка."""
