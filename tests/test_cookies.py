from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.settings import Config


def test_filter_cookies_by_domain():
    dl = VKMediaDownloader(Config())
    cookies = [
        {"name": "a", "value": "1", "domain": "okcdn.ru", "path": "/"},
        {"name": "b", "value": "2", "domain": "vkvideo.ru", "path": "/"},
        {"name": "c", "value": "3", "domain": "evil.com", "path": "/"},
    ]
    filtered = dl._filter_cookies(cookies, "https://vkvd123.okcdn.ru/video/seg.m4s", None, None, None)
    names = {c["name"] for c in filtered}
    assert "a" in names
    assert "b" not in names  # vkvideo not suffix of okcdn host
    assert "c" not in names


def test_filter_cookies_keeps_all_when_no_url():
    dl = VKMediaDownloader(Config())
    cookies = [{"name": "a", "value": "1"}]
    assert len(dl._filter_cookies(cookies, None, None, None, None)) == 1


def test_filter_cookies_host_only():
    dl = VKMediaDownloader(Config())
    cookies = [{"name": "a", "value": "1"}]  # no domain
    filtered = dl._filter_cookies(cookies, "https://vkvd123.okcdn.ru/seg", None, None, None)
    assert len(filtered) == 1
