"""Тесты парсера MPD (без обращения к сети)."""

import pytest

from vk_downloader.media.mpd_parser import MPDParser

STATIC_MPD = (
    '<?xml version="1.0"?>'
    '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">'
    "<Period>"
    '<AdaptationSet mimeType="video/mp4">'
    '<Representation id="1" bandwidth="500" height="360" codecs="avc1.64001e" mimeType="video/mp4">'
    "<BaseURL>seg.mp4</BaseURL></Representation>"
    "</AdaptationSet>"
    '<AdaptationSet mimeType="audio/mp4">'
    '<Representation id="2" bandwidth="128000" codecs="mp4a.40.2" mimeType="audio/mp4">'
    "<BaseURL>audio.mp4</BaseURL></Representation>"
    "</AdaptationSet>"
    "</Period></MPD>"
)


def test_parse_static_mpd_returns_video_and_audio():
    videos, audios = MPDParser.parse(STATIC_MPD, "https://example.com/manifest.mpd")
    assert len(videos) == 1
    assert len(audios) == 1


def test_video_track_fields():
    videos, _ = MPDParser.parse(STATIC_MPD, "https://example.com/manifest.mpd")
    track = videos[0]
    assert track["height"] == 360
    assert track["codecs"] == "avc1.64001e"
    assert track["mime"] == "video/mp4"


def test_baseurl_resolution():
    videos, audios = MPDParser.parse(STATIC_MPD, "https://example.com/manifest.mpd")
    assert videos[0]["url"].endswith("seg.mp4")
    assert audios[0]["url"].endswith("audio.mp4")


def test_invalid_xml_raises():
    with pytest.raises(RuntimeError):
        MPDParser.parse("not xml", "https://example.com/manifest.mpd")


def test_no_tracks_raises():
    empty = '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period></Period></MPD>'
    with pytest.raises(RuntimeError):
        MPDParser.parse(empty, "https://example.com/m.mpd")
