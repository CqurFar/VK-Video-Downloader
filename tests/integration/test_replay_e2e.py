"""E2E replay: реальный пайплайн загрузки против локального CDN (маркер integration)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fixtures import ABUSIVE_MPD, REPLAY_MPD

from vk_downloader.core.errors import MPDTooLargeError
from vk_downloader.download.dash_downloader import DashDownloader
from vk_downloader.media.mpd_parser import MPDParser
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console


@pytest.mark.integration
def test_download_video_track_replay(replay_cdn: str, tmp_path: Path):
    """Реальный download_track качает сегменты с локального CDN и собирает файл."""
    mpd_url = f"{replay_cdn}/manifest.mpd"
    videos, _ = MPDParser.parse(REPLAY_MPD, mpd_url)
    assert videos, "MPD должен дать хотя бы один видео-трек"
    track = videos[0]

    cfg = Config()
    downloader = DashDownloader(cfg, Console(cfg))
    output = tmp_path / "out.mp4"
    headers = DashDownloader.media_headers("Mozilla/5.0 (replay-test)", mpd_url, None)

    asyncio.run(
        downloader.download_track(track, None, mpd_url, headers, output, "Video Track", tmp_path)
    )

    assert output.is_file(), "итоговый файл должен быть собран"
    data = output.read_bytes()
    # init (ftypisom) + оба видео-сегмента
    assert b"ftypisom" in data
    assert b"VIDEO-SEG-1" in data and b"VIDEO-SEG-2" in data


@pytest.mark.integration
def test_mpd_too_large_rejected():
    """Аномально большой r= в Timeline отклоняется на этапе парсинга (защита от DoS)."""
    with pytest.raises(MPDTooLargeError):
        MPDParser.parse(ABUSIVE_MPD, "https://cdn.vkuser.ru/manifest.mpd")
