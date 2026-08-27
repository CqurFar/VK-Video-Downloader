"""Тесты выбора качества: совместимость кодеков с контейнером."""

import pytest

from vk_downloader.core.errors import QualityNotAvailableError
from vk_downloader.core.quality import QualitySelector
from vk_downloader.settings import Config


def _selector() -> QualitySelector:
    return QualitySelector(Config())


def test_mkv_keeps_vp9_track():
    sel = _selector()
    videos = [{"codecs": "vp09.00.31.08", "height": 720, "bandwidth": 1000}]
    chosen = sel.choose_video(videos, "best", "mkv")
    assert chosen["codecs"].startswith("vp09")


def test_mkv_keeps_av1_track():
    sel = _selector()
    videos = [{"codecs": "av01.0.08M.08", "height": 1080, "bandwidth": 2000}]
    chosen = sel.choose_video(videos, "best", "mkv")
    assert chosen["codecs"].startswith("av01")


def test_webm_still_rejects_avc1():
    sel = _selector()
    videos = [{"codecs": "avc1.64001e", "height": 720, "bandwidth": 1000}]
    try:
        sel.choose_video(videos, "best", "webm")
        pytest.fail("expected QualityNotAvailableError")
    except QualityNotAvailableError:
        pass


def test_mp4_still_uses_mp4_whitelist():
    sel = _selector()
    videos = [
        {"codecs": "avc1.64001e", "height": 720, "bandwidth": 1000},
        {"codecs": "vp09.00.31.08", "height": 1080, "bandwidth": 2000},
    ]
    chosen = sel.choose_video(videos, "best", "mp4")
    assert chosen["codecs"].startswith("avc1")
