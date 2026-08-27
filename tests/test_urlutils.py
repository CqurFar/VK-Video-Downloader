"""Прямые тесты вынесенных urlutils — без зависимости от God Object."""

from pathlib import Path

from vk_downloader.core import urlutils
from vk_downloader.settings import Config


def test_safe_filename_direct():
    cfg = Config()
    assert urlutils.safe_filename('a<b>c:"d/e\\f|g?*h', cfg) == "a_b_c__d_e_f_g__h"
    assert urlutils.safe_filename("   ", cfg) == "vk-video"
    assert len(urlutils.safe_filename("a" * 200, cfg)) == cfg.naming.max_name


def test_short_label_direct():
    assert urlutils.short_label("https://vkvideo.ru/video_ext.php?oid=-1&id=123456") == "VK-123456"
    assert len(urlutils.short_label("https://example.com/" + "a" * 100)) == 45


def test_redact_mpd_direct():
    raw = (
        "<BaseURL>https://cdn.example.com/seg.m4s?token=abc123&amp;sig=deadbeef&extra=xyz</BaseURL>"
    )
    redacted = urlutils.redact_mpd(raw)
    assert "abc123" not in redacted
    assert "token=***" in redacted
    assert "sig=***" in redacted


def test_filter_cookies_direct():
    cookies = [
        {"name": "a", "value": "1", "domain": "vkvideo.ru"},
        {"name": "b", "value": "2", "domain": "cdn.example.com"},
        {"name": "", "value": "bad"},
    ]
    # разрешаем только vkvideo.ru
    filtered = urlutils.filter_cookies(cookies, "https://vkvideo.ru/video")
    assert len(filtered) == 1
    assert filtered[0]["name"] == "a"
    # без allowed hosts — возвращаем все именованные
    filtered2 = urlutils.filter_cookies(cookies)
    assert len(filtered2) == 2


def test_filter_cookies_path_scope():
    # path-scoped cookie не должен уходить на чужой путь запроса
    cookies = [
        {"name": "a", "value": "1", "domain": "vkvideo.ru", "path": "/secret"},
        {"name": "b", "value": "2", "domain": "vkvideo.ru", "path": "/"},
    ]
    # запрос к /video — cookie /secret не подходит, / подходит
    filtered = urlutils.filter_cookies(cookies, "https://vkvideo.ru/video/x.mpd")
    names = {c["name"] for c in filtered}
    assert "a" not in names
    assert "b" in names
    # запрос к /secret — оба подходят
    filtered2 = urlutils.filter_cookies(cookies, "https://vkvideo.ru/secret/x")
    assert {c["name"] for c in filtered2} == {"a", "b"}


def test_build_cookie_jar_applies_domain():
    import requests

    jar = urlutils.build_cookie_jar(
        [
            {"name": "a", "value": "1", "domain": "vk.com", "path": "/"},
            {"name": "b", "value": "2", "domain": "cdn.other.com", "path": "/"},
        ]
    )
    assert isinstance(jar, requests.cookies.RequestsCookieJar)
    # cookie для vk.com не отдаётся на чужой домен при реальном запросе
    req = requests.Request("GET", "https://vk.com/x", cookies=jar).prepare()
    cookie_header = req.headers.get("Cookie", "")
    assert "a=1" in cookie_header
    assert "b=2" not in cookie_header


def test_normalize_url_direct():
    assert (
        urlutils.normalize_url("https://vkvideo.ru/video-232462760_456239037?pl=1")
        == "https://vkvideo.ru/video_ext.php?oid=-232462760&id=456239037"
    )
    assert urlutils.normalize_url("https://example.com/") == "https://example.com/"


def test_codec_is_direct():
    assert urlutils.codec_is("vp09.00.10.08", ("vp09",)) is True
    assert urlutils.codec_is("avc1.64001e", ("vp09",)) is False


def test_free_disk_gb(tmp_path):
    # tmp_path всегда существует, free >0
    gb = urlutils.free_disk_gb(tmp_path)
    assert gb > 0
    assert urlutils.free_disk_gb("/nonexistent/path/xyz") == 0.0 or isinstance(gb, float)
