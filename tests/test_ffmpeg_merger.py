"""Unit-тесты для FFmpegMerger — логика склейки без реального ffmpeg."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vk_downloader.media.ffmpeg_merger import FFmpegMerger
from vk_downloader.settings import Config


def _merger(tmp_path=None, **overrides):
    cfg = Config()
    if tmp_path is not None:
        cfg.paths.output_dir = tmp_path
        cfg.paths.ffmpeg_dir = tmp_path / "ffmpeg"
    for k, v in overrides.items():
        setattr(cfg.media, k, v)
    console = MagicMock()
    console.log = MagicMock()
    console.progress = MagicMock()
    return FFmpegMerger(cfg, console), cfg


def test_build_command_video_audio_copy_mkv():
    merger, _ = _merger()
    cmd = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        Path("/tmp/a.m4s"),
        Path("/tmp/out.mkv"),
        "mkv",
        {"codecs": "avc1.64001e"},
        {"codecs": "mp4a.40.2"},
    )
    assert "-map" in cmd
    assert cmd.count("-map") == 2
    assert "-c" in cmd and "copy" in cmd
    assert "-movflags" not in cmd  # mkv не нужен faststart
    assert "-progress" in cmd and "pipe:1" in cmd


def test_build_command_video_audio_mp4_faststart():
    merger, _ = _merger()
    cmd = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        Path("/tmp/a.m4s"),
        Path("/tmp/out.mp4"),
        "mp4",
        {"codecs": "avc1.64001e"},
        {"codecs": "mp4a.40.2"},
    )
    assert "-movflags" in cmd
    assert "+faststart" in cmd


def test_build_command_video_only():
    merger, _ = _merger()
    cmd = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        None,
        Path("/tmp/out.mkv"),
        "mkv",
        {"codecs": "avc1.64001e"},
        None,
    )
    assert cmd.count("-i") == 1
    assert "0:v:0" in cmd
    assert "-c" in cmd

    cmd_mp4 = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        None,
        Path("/tmp/out.mp4"),
        "mp4",
        {"codecs": "avc1.64001e"},
        None,
    )
    assert "+faststart" in cmd_mp4


def test_build_command_audio_only_mp3():
    merger, _ = _merger()
    cmd = merger.build_command(
        "ffmpeg",
        None,
        Path("/tmp/a.m4s"),
        Path("/tmp/out.mp3"),
        "mp3",
        None,
        {"codecs": "mp4a.40.2"},
    )
    assert cmd.count("-i") == 1
    assert "0:a:0" in cmd
    assert "libmp3lame" in cmd


def test_build_command_audio_only_aac_copy_vs_reencode():
    merger, _ = _merger()
    # aac source -> copy
    cmd_copy = merger.build_command(
        "ffmpeg", None, Path("/tmp/a.m4s"), Path("/tmp/out.m4a"), "m4a", None, {"codecs": "mp4a.40.2"}
    )
    idx = cmd_copy.index("-c:a")
    assert cmd_copy[idx + 1] == "copy"
    # non-aac source -> re-encode
    cmd_enc = merger.build_command(
        "ffmpeg", None, Path("/tmp/a.m4s"), Path("/tmp/out.m4a"), "m4a", None, {"codecs": "opus"}
    )
    idx2 = cmd_enc.index("-c:a")
    assert cmd_enc[idx2 + 1] == "aac"


def test_build_command_audio_target_format_inside_mkv():
    merger, cfg = _merger()
    cfg.media.audio_target_format = "aac"
    cmd = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        Path("/tmp/a.m4s"),
        Path("/tmp/out.mkv"),
        "mkv",
        {"codecs": "avc1.64001e"},
        {"codecs": "opus"},
    )
    # видео копией, аудио конвертацией
    assert "-c:v" in cmd and "copy" in cmd
    assert "-c:a" in cmd


def test_build_command_webm_requires_vp9_opus():
    merger, _ = _merger()
    # корректная пара — не падает
    merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        Path("/tmp/a.m4s"),
        Path("/tmp/out.webm"),
        "webm",
        {"codecs": "vp09.00.10.08"},
        {"codecs": "opus"},
    )
    # неверная пара — падает
    with pytest.raises(RuntimeError, match="WebM requires"):
        merger.build_command(
            "ffmpeg",
            Path("/tmp/v.m4s"),
            Path("/tmp/a.m4s"),
            Path("/tmp/out.webm"),
            "webm",
            {"codecs": "avc1.64001e"},
            {"codecs": "mp4a.40.2"},
        )


def test_build_command_both_missing_raises():
    merger, _ = _merger()
    with pytest.raises(RuntimeError, match="Nothing to merge"):
        merger.build_command("ffmpeg", None, None, Path("/tmp/out.mkv"), "mkv", None, None)


def test_build_command_extra_args_appended():
    merger, cfg = _merger()
    cfg.media.ffmpeg_extra_args = ["-metadata", "title=Test"]
    cmd = merger.build_command(
        "ffmpeg",
        Path("/tmp/v.m4s"),
        Path("/tmp/a.m4s"),
        Path("/tmp/out.mkv"),
        "mkv",
        {"codecs": "avc1.64001e"},
        {"codecs": "mp4a.40.2"},
    )
    # extra args должны быть перед -progress
    assert "-metadata" in cmd
    assert cmd.index("-metadata") < cmd.index("-progress")


def test_audio_codec_args_matrix():
    m = FFmpegMerger
    assert m.audio_codec_args("mp3", "anything") == ["-c:a", "libmp3lame"]
    assert m.audio_codec_args("wav", "anything") == ["-c:a", "pcm_s16le"]
    assert m.audio_codec_args("opus", "opus") == ["-c:a", "copy"]
    assert m.audio_codec_args("opus", "aac") == ["-c:a", "libopus"]
    assert m.audio_codec_args("ogg", "vorbis") == ["-c:a", "copy"]
    assert m.audio_codec_args("ogg", "aac") == ["-c:a", "libvorbis"]
    assert m.audio_codec_args("aac", "mp4a.40.2") == ["-c:a", "copy"]
    assert m.audio_codec_args("aac", "opus") == ["-c:a", "aac"]
    with pytest.raises(RuntimeError, match="Unsupported audio format"):
        m.audio_codec_args("mkv", "aac")


def test_codec_family():
    m = FFmpegMerger
    assert m._codec_family("vp09.00.10.08", ("vp8", "vp9", "vp09", "av01")) is True
    assert m._codec_family("avc1.64001e", ("vp8", "vp9", "vp09", "av01")) is False
    assert m._codec_family("  VP09.00.10.08  ", ("vp09",)) is True


def test_locate_prefers_packages_over_path(tmp_path, monkeypatch):
    fake_ffmpeg = tmp_path / "ffmpeg" / "ffmpeg.exe"
    fake_ffmpeg.parent.mkdir(parents=True)
    fake_ffmpeg.write_bytes(b"fake")
    merger, _ = _merger(tmp_path)
    # PATH не нужен — найдётся в packages
    monkeypatch.setattr("shutil.which", lambda x: None)
    # укажем ffmpeg_dir явно
    merger.config.paths.ffmpeg_dir = tmp_path / "ffmpeg"
    assert merger.locate() == str(fake_ffmpeg.resolve())
    # без файла — fallback в PATH
    fake_ffmpeg.unlink()
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ffmpeg" if x == "ffmpeg" else None)
    assert merger.locate() == "/usr/bin/ffmpeg"


def test_locate_returns_none_when_not_found(tmp_path, monkeypatch):
    merger, _ = _merger(tmp_path)
    merger.config.paths.ffmpeg_dir = tmp_path / "nope"
    monkeypatch.setattr("shutil.which", lambda x: None)
    assert merger.locate() is None
    with pytest.raises(Exception, match="ffmpeg was not found"):
        merger.find_ffmpeg()


def test_codec_name():
    m = FFmpegMerger
    assert m.codec_name({"codecs": "avc1.64001e"}) == "avc1.64001e"
    assert m.codec_name({"mime": "video/mp4"}) == "video/mp4"
    assert m.codec_name(None) == ""
    assert m.codec_name({}) == ""
