import asyncio
from pathlib import Path

import pytest
import requests

from vk_downloader.download.dash_downloader import DashDownloader
from vk_downloader.media.mpd_parser import MPDParser
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console


@pytest.fixture()
def downloader() -> DashDownloader:
    return DashDownloader(Config(), Console(Config()))


def _make_jar() -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    jar.set("remixsid", "secret", domain="vk.com", path="/")
    return jar


class _FakeResponse:
    def __init__(self, data: bytes):
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._data = data

    def iter_content(self, chunk_size=1024 * 1024):
        yield self._data

    def close(self):
        pass


def test_download_resource_forwards_cookies(downloader, tmp_path, monkeypatch):
    jar = _make_jar()
    captured: dict = {}

    class _FakeSession:
        def __init__(self, response: _FakeResponse):
            self._response = response

        def get(self, url, timeout=None, stream=None, cookies=None):
            captured["cookies"] = cookies
            return self._response

    monkeypatch.setattr(
        downloader, "_session", lambda headers: _FakeSession(_FakeResponse(b"x" * 64))
    )

    target = tmp_path / "part.bin"
    total = downloader._download_resource(str(target), {}, target, "seg", cookies=jar)

    assert total == 64
    assert captured["cookies"] is jar


async def _fake_single_file(track, headers, output, label, cookies=None):
    _fake_single_file.captured = cookies


def test_download_track_single_file_forwards_cookies(downloader, monkeypatch):
    jar = _make_jar()
    monkeypatch.setattr(downloader, "_download_single_file", _fake_single_file)

    track = {"url": "https://vkvideo.ru/file.mp4"}
    asyncio.run(
        downloader.download_track(
            track, None, "https://vkvideo.ru/x.mpd", {},
            Path("o"), "v", Path("."), cookies=jar,
        )
    )

    assert _fake_single_file.captured is jar


def test_download_track_segments_forwards_cookies(downloader, tmp_path, monkeypatch):
    jar = _make_jar()
    captured: list = []

    def recorder(url, headers, output, label, cookies=None):
        captured.append(cookies)
        return 0

    monkeypatch.setattr(downloader, "_download_resource", recorder)
    monkeypatch.setattr(downloader, "assemble", lambda *a, **k: None)

    track = {
        "segment_template": {"media": "$Number$", "initialization": "init.mp4"},
        "segment_timeline": [{"number": 1, "time": 0}, {"number": 2, "time": 1}],
    }
    asyncio.run(
        downloader.download_track(
            track, None, "https://vkvideo.ru/x.mpd", {},
            tmp_path / "o", "v", tmp_path, cookies=jar,
        )
    )

    assert captured, "expected _download_resource to be called"
    assert all(c is jar for c in captured)


def test_looks_like_html(tmp_path):
    html = tmp_path / "a.bin"
    html.write_bytes(b"  \n<!DOCTYPE html><html></html>")
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x00\x00\x18ftypisom")

    assert DashDownloader._looks_like_html(html) is True
    assert DashDownloader._looks_like_html(binary) is False


def test_mpd_rejects_entity_declarations():
    malicious = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xx "a">]><MPD></MPD>'

    with pytest.raises(RuntimeError, match="DTD/entity"):
        MPDParser.parse(malicious, "https://vkvideo.ru/x.mpd")
