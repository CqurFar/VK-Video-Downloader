"""Тесты file-level retry с учётом is_retryable и playlist extraction."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.core.errors import QualityNotAvailableError
from vk_downloader.settings import Config


@pytest.fixture
def dl():
    cfg = Config()
    cfg.download.auto_retries = 2
    cfg.download.auto_retry_delay = 0  # no sleep in tests
    cfg.paths.failed_file = cfg.paths.failed_file  # keep default
    d = VKMediaDownloader(cfg)
    # не нужен реальный браузер/консоль для этих тестов — мокаем консоль
    d.console = MagicMock()
    d.console.is_normal.return_value = False
    d.console.video_event = MagicMock()
    d.console.item_status = MagicMock()
    d.console.scan_start = MagicMock()
    return d


def test_process_with_retry_non_retryable_no_retry(dl):
    """TypeError — программистская ошибка — не должна ретраиться."""
    async def fail_process(*a, **kw):
        raise TypeError("bad programming")

    dl.process_one = AsyncMock(side_effect=fail_process)

    async def run():
        res = await dl._process_with_retry(MagicMock(), "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None)
        return res

    res = asyncio.run(run())
    assert res["status"] == "ERROR"
    # process_one должна быть вызвана ровно 1 раз, а не 3 (retries+1)
    assert dl.process_one.call_count == 1


def test_process_with_retry_quality_not_available_no_retry(dl):
    async def fail_process(*a, **kw):
        raise QualityNotAvailableError("no 1080")

    dl.process_one = AsyncMock(side_effect=fail_process)

    async def run():
        return await dl._process_with_retry(MagicMock(), "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None)

    res = asyncio.run(run())
    assert res["status"] == "ERROR"
    assert dl.process_one.call_count == 1


def test_process_with_retry_transient_retries(dl):
    """RuntimeError — транзиентная — должна ретраиться."""
    calls = 0

    async def flaky(*a, **kw):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("cdn hiccup")
        return "ok-title"

    dl.process_one = AsyncMock(side_effect=flaky)
    dl.config.download.auto_retries = 3

    async def run():
        return await dl._process_with_retry(MagicMock(), "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None)

    res = asyncio.run(run())
    assert res["status"] == "DONE"
    assert calls == 3


def test_download_with_retry_non_retryable_breaks(dl):
    """_download_with_retry тоже не ретраит программистские ошибки."""
    dl._materialize = AsyncMock(side_effect=TypeError("oops"))

    async def run():
        return await dl._download_with_retry({}, "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None)

    res = asyncio.run(run())
    assert res["status"] == "ERROR"
    assert dl._materialize.call_count == 1


def test_extract_with_retry_non_retryable_returns_none_immediately(dl):
    """_extract_with_retry: QualityNotAvailableError — сразу None."""
    dl.browser_helper.extract_playlist = AsyncMock(side_effect=QualityNotAvailableError("bad playlist"))

    async def run():
        return await dl._extract_with_retry(MagicMock(), "https://vkvideo.ru/playlist/-1_1")

    res = asyncio.run(run())
    assert res is None
    assert dl.browser_helper.extract_playlist.call_count == 1


def test_extract_with_retry_transient_retries(dl):
    calls = 0

    async def flaky(browser, playlist):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("timeout")
        return ("My Playlist", ["https://vkvideo.ru/video-1_1"])

    dl.browser_helper.extract_playlist = AsyncMock(side_effect=flaky)

    async def run():
        return await dl._extract_with_retry(MagicMock(), "https://vkvideo.ru/playlist/-1_1")

    res = asyncio.run(run())
    assert res is not None
    assert res[0] == "My Playlist"
    assert calls == 2
