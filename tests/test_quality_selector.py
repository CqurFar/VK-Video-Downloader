"""Тесты выбора ближайшего качества — должен брать ближайшее большее, а не max."""

import pytest

from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.core.errors import QualityNotAvailableError
from vk_downloader.settings import Config


@pytest.fixture
def dl():
    return VKMediaDownloader(Config())


def _video_tracks():
    return [
        {"height": 720, "bandwidth": 1000, "codecs": "avc1.64001e", "id": "720"},
        {"height": 1080, "bandwidth": 2000, "codecs": "avc1.640028", "id": "1080"},
        {"height": 1440, "bandwidth": 4000, "codecs": "avc1.640033", "id": "1440"},
    ]


def _audio_tracks():
    return [
        {"bandwidth": 64000, "codecs": "mp4a.40.2", "id": "64k"},
        {"bandwidth": 128000, "codecs": "mp4a.40.2", "id": "128k"},
        {"bandwidth": 192000, "codecs": "mp4a.40.2", "id": "192k"},
    ]


def test_pick_video_nearest_greater_not_max(dl):
    # 900p → ближайшее большее 1080, а не 1440
    track = dl._pick("video", _video_tracks(), "900")
    assert track["height"] == 1080


def test_pick_video_exact_match(dl):
    track = dl._pick("video", _video_tracks(), "1080")
    assert track["height"] == 1080


def test_pick_video_below_min_returns_min(dl):
    track = dl._pick("video", _video_tracks(), "100")
    assert track["height"] == 720


def test_pick_video_above_max_returns_max(dl):
    track = dl._pick("video", _video_tracks(), "2000")
    assert track["height"] == 1440


def test_pick_video_best_returns_max(dl):
    track = dl._pick("video", _video_tracks(), "best")
    assert track["height"] == 1440


def test_pick_audio_nearest_greater(dl):
    # 96 kbps → 128k, а не 192k
    track = dl._pick("audio", _audio_tracks(), "96")
    assert track["bandwidth"] == 128000


def test_pick_audio_exact(dl):
    track = dl._pick("audio", _audio_tracks(), "128")
    assert track["bandwidth"] == 128000


def test_choose_video_int_target_nearest(dl):
    # choose_video с int 900 → 1080
    track = dl.choose_video(_video_tracks(), 900, "mkv")
    assert track["height"] == 1080


def test_choose_video_best(dl):
    track = dl.choose_video(_video_tracks(), "best", "mkv")
    assert track["height"] == 1440


def test_choose_audio_int_target_nearest(dl):
    track = dl.choose_audio(_audio_tracks(), 96, "mkv")
    assert track["bandwidth"] == 128000


def test_pick_invalid_raises(dl):
    with pytest.raises(QualityNotAvailableError):
        dl._pick("video", _video_tracks(), "bad")
