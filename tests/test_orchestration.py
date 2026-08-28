"""Тесты оркестратора — чистые helpers без браузера/сети."""

import argparse

import pytest

from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.core.errors import QualityNotAvailableError
from vk_downloader.settings import Config


@pytest.fixture
def dl():
    return VKMediaDownloader(Config())


def test_safe_filename_sanitizes(dl):
    assert dl.safe_filename('a<b>c:"d/e\\f|g?*h') == "a_b_c__d_e_f_g__h"
    assert dl.safe_filename("   ") == "vk-video"


def test_safe_filename_truncates(dl):
    long = "a" * 200
    assert len(dl.safe_filename(long)) == dl.config.naming.max_name


def test_codec_is(dl):
    assert dl._codec_is("avc1.64001e", ("avc1", "hvc1")) is True
    assert dl._codec_is("vp09.00.10.08", ("vp09",)) is True
    assert dl._codec_is("mp4a.40.2", ("avc1",)) is False
    assert dl._codec_is("VP09.00", ("vp09",)) is True  # case-insensitive


def test_normalize_url(dl):
    url = "https://vkvideo.ru/video-232462760_456239037?pl=1"
    assert dl.normalize_url(url) == "https://vkvideo.ru/video_ext.php?oid=-232462760&id=456239037"
    # не видео-ссылка — возвращает как есть
    assert dl.normalize_url("https://example.com/") == "https://example.com/"


def test_parse_format_video_only(dl):
    parser = argparse.ArgumentParser()
    assert dl._parse_format(parser, "mp4") == "mp4"
    assert dl._parse_format(parser, "mkv") == "mkv"


def test_parse_format_video_plus_audio(dl):
    parser = argparse.ArgumentParser()
    assert dl._parse_format(parser, "mkv+aac") == "mkv"
    assert dl.config.media.audio_target_format == "aac"


def test_parse_format_audio_only(dl):
    parser = argparse.ArgumentParser()
    assert dl._parse_format(parser, "mp3") == "mp3"


def test_parse_format_acc_alias(dl):
    parser = argparse.ArgumentParser()
    assert dl._parse_format(parser, "mkv+acc") == "mkv"
    assert dl.config.media.audio_target_format == "aac"


def test_parse_format_invalid_raises(dl):
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        dl._parse_format(parser, "bad+bad+bad")


def test_short_label(dl):
    url = "https://vkvideo.ru/video_ext.php?oid=-1&id=123456"
    assert dl._short_label(url) == "VK-123456"
    assert len(dl._short_label("https://example.com/" + "a" * 100)) == 45


def test_redact_mpd_masks_tokens(dl):
    raw = '<BaseURL>https://cdn.example.com/seg.m4s?token=abc123&amp;sig=deadbeef&extra=xyz</BaseURL>'
    redacted = dl._redact_mpd(raw)
    assert "abc123" not in redacted
    assert "deadbeef" not in redacted
    assert "token=***" in redacted
    assert "sig=***" in redacted
    # нечувствительные параметры не трогаем (если вдруг)
    assert "https://cdn.example.com/seg.m4s" in redacted


def test_redact_mpd_no_false_positive(dl):
    txt = "no query here"
    assert dl._redact_mpd(txt) == txt
