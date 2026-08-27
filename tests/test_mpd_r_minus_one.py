"""Проверка robustness r=-1 tail — warning когда duration неизвестна."""

import logging

from vk_downloader.media.mpd_parser import MPDParser


def test_r_minus_one_last_no_duration_logs_warning(caplog):
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S d="1000" r="-1"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    with caplog.at_level(logging.WARNING, logger="vk_downloader.media.mpd_parser"):
        videos, _ = MPDParser.parse(xml, "https://example.com/manifest.mpd")
    assert len(videos[0]["segment_timeline"]) == 1
    assert any("r=-1" in r.message for r in caplog.records)


def test_r_minus_one_last_with_duration_no_warning(caplog):
    xml = (
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT0H0M5.0S">'
        '<Period duration="PT5S">'
        '<AdaptationSet mimeType="video/mp4">'
        '<Representation id="1" height="360" bandwidth="500" codecs="avc1.64001e">'
        "<BaseURL>https://cdn.example.com/</BaseURL>"
        '<SegmentTemplate timescale="1000" media="$Number$.m4s">'
        '<SegmentTimeline><S t="0" d="1000" r="-1"/></SegmentTimeline>'
        "</SegmentTemplate></Representation></AdaptationSet></Period></MPD>"
    )
    with caplog.at_level(logging.WARNING, logger="vk_downloader.media.mpd_parser"):
        videos, _ = MPDParser.parse(xml, "https://example.com/manifest.mpd")
    assert len(videos[0]["segment_timeline"]) == 5
    assert not any("r=-1" in r.message for r in caplog.records)


def test_vk_alias_import():
    from vk_downloader.media.mpd_parser import VKMPDParser

    assert VKMPDParser is MPDParser
    assert MPDParser.VKMPDParser is MPDParser
