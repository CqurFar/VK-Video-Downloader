"""Trust boundary: VK host allowlist for input/MPD/segment URLs."""

from vk_downloader.browser.media_browser import VKMediaBrowser
from vk_downloader.core import urlutils
from vk_downloader.core.errors import InvalidURLError
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console


def _browser():
    return VKMediaBrowser(Config(), Console(Config()))


def test_is_vk_host():
    assert urlutils.is_vk_host("https://vkvideo.ru/video_ext.php?oid=1&id=2")
    assert urlutils.is_vk_host("https://vk.com/x")
    assert urlutils.is_vk_host("https://www.vk.ru/x")  # www. stripped
    assert not urlutils.is_vk_host("http://vkvideo.ru/x")  # http rejected
    assert not urlutils.is_vk_host("https://evil.com/x")


def test_is_vk_cdn():
    assert urlutils.is_vk_cdn("https://vkvd123.okcdn.ru/seg.m4s")
    assert urlutils.is_vk_cdn("https://vk6-15.vkuser.net/seg.m4s")
    assert not urlutils.is_vk_cdn("https://evil.com/seg.m4s")


def test_validate_input_url():
    # нормализуется и принимается
    assert urlutils.validate_input_url("https://vkvideo.ru/video-232462760_456239037").startswith(
        "https://vkvideo.ru/video_ext.php"
    )
    # чужой хост — отклоняется до любого сетевого вызова
    try:
        urlutils.validate_input_url("https://evil.example.com/video-1_2")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_candidate_rejects_foreign_mpd():
    # https://random.example/video.mpd не проходит, даже с .mpd в пути
    assert _browser()._is_candidate("https://random.example.com/video.mpd") is False
    # доверенный хост VK с .mpd — принимается
    assert _browser()._is_candidate("https://vkvd123.okcdn.ru/path/manifest.mpd") is True


def test_segment_data_keeps_only_vk_cdn():
    b = _browser()
    resources = [
        "https://evil.com/track.v.m4s",
        "https://vkvd123.okcdn.ru/fn/s1.v.m4s",
        "https://vk6.vkuser.net/fn/s1.a.m4s",
    ]
    data = b._extract_segment_data(resources)
    # чужой хост отброшен, остались только CDN VK
    assert data["video_segment_url"] == "https://vkvd123.okcdn.ru/fn/s1.v.m4s"
    assert data["audio_segment_url"] == "https://vk6.vkuser.net/fn/s1.a.m4s"


def test_invalid_url_is_non_retryable():
    # Ошибка валидации входной ссылки не должна вызывать бесконечных ретраев
    assert not __import__("vk_downloader.core.retry", fromlist=["is_retryable"]).is_retryable(
        InvalidURLError("bad")
    )
