"""Unit-тесты DashDownloader — чистые helpers без сети."""

import tempfile
from pathlib import Path

from vk_downloader.download.dash_downloader import DashDownloader
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console


def _dl():
    return DashDownloader(Config(), Console(Config()))


def test_media_headers():
    dl = _dl()
    h = dl.media_headers("Mozilla/5.0", "https://vkvideo.ru/video-1_123", "https://cdn.example.com/seg.m4s")
    assert h["User-Agent"] == "Mozilla/5.0"
    assert "cdn.example.com" in h["Referer"]
    assert "vkvideo.ru" in h["Origin"]


def test_resolve_segment_url_number():
    assert _dl().resolve_segment_url("https://cdn.example.com/", "seg-$Number$.m4s", number=5) == "https://cdn.example.com/seg-5.m4s"


def test_resolve_segment_url_time():
    assert _dl().resolve_segment_url("https://cdn.example.com/", "seg-$Time$.m4s", time_value=12345) == "https://cdn.example.com/seg-12345.m4s"


def test_resolve_segment_url_empty():
    assert _dl().resolve_segment_url("https://cdn.example.com/", "") == ""


def test_resolve_segment_url_slash_normalization():
    # "\\/" → "/"
    assert _dl().resolve_segment_url("https://cdn.example.com/", "a\\/b.m4s") == "https://cdn.example.com/a/b.m4s"


def test_segment_base_detected_priority():
    dl = _dl()
    track = {"url": "https://cdn.example.com/ondemand/abc/"}
    base = dl.segment_base(track, "https://detected.example.com/path/", "https://example.com/manifest.mpd")
    assert base == "https://detected.example.com/path/"


def test_segment_base_ondemand_fallback():
    dl = _dl()
    track = {"url": "https://cdn.example.com/ondemand/video_123/"}
    base = dl.segment_base(track, None, "https://example.com/manifest.mpd")
    assert base == "https://cdn.example.com/ondemand/"


def test_segment_base_mpd_fallback():
    dl = _dl()
    track = {"url": ""}
    base = dl.segment_base(track, None, "https://example.com/path/manifest.mpd")
    assert base == "https://example.com/path/"


def test_part_path_format():
    p = DashDownloader._part_path(Path("/tmp/parts"), 0)
    assert p.name == "00000001.part"
    assert DashDownloader._part_path(Path("/tmp"), 9).name == "00000010.part"


def test_valid_part(tmp_path):
    p = tmp_path / "00000001.part"
    p.write_bytes(b"data")
    assert DashDownloader._valid_part(p) is True
    assert DashDownloader._valid_part(tmp_path / "missing.part") is False
    empty = tmp_path / "empty.part"
    empty.write_bytes(b"")
    assert DashDownloader._valid_part(empty) is False


def test_assemble(tmp_path):
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    (parts_dir / "00000000.init").write_bytes(b"INIT")
    (parts_dir / "00000001.part").write_bytes(b"A")
    (parts_dir / "00000002.part").write_bytes(b"B")
    out = tmp_path / "out.m4s"
    DashDownloader.assemble(parts_dir, 2, out)
    assert out.read_bytes() == b"INITAB"
