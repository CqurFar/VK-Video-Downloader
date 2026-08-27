"""Прямые тесты QualitySelector — импортируется из core/quality без God Object."""

import pytest

from vk_downloader.core.errors import QualityNotAvailableError
from vk_downloader.core.quality import QualitySelector
from vk_downloader.settings import Config


def _tracks_video():
    return [
        {"height": 720, "bandwidth": 1000, "codecs": "avc1.64001e", "id": "720"},
        {"height": 1080, "bandwidth": 2000, "codecs": "avc1.640028", "id": "1080"},
        {"height": 1440, "bandwidth": 4000, "codecs": "avc1.640033", "id": "1440"},
    ]


def test_quality_selector_nearest_greater():
    qs = QualitySelector(Config())
    track = qs._pick("video", _tracks_video(), "900")
    assert track["height"] == 1080


def test_quality_selector_choose_video_delegates():
    qs = QualitySelector(Config())
    track = qs.choose_video(_tracks_video(), 900, "mkv")
    assert track["height"] == 1080


def test_quality_selector_webm_filters_codecs():
    qs = QualitySelector(Config())
    tracks = [
        {"height": 720, "bandwidth": 1000, "codecs": "avc1.64001e", "id": "avc"},
        {"height": 720, "bandwidth": 1000, "codecs": "vp09.00.10.08", "id": "vp9"},
    ]
    # webm должен оставить только vp9
    track = qs.choose_video(tracks, "best", "webm")
    assert track["codecs"].startswith("vp09")
    # mp4 предпочитает avc, но не требует
    track2 = qs.choose_video(tracks, "best", "mp4")
    assert track2["codecs"].startswith("avc1")


def test_quality_selector_invalid_raises():
    qs = QualitySelector(Config())
    with pytest.raises(QualityNotAvailableError):
        qs._pick("video", _tracks_video(), "bad")
