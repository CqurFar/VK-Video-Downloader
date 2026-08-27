"""Pipeline mode — producer/consumer без реального браузера."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.settings import Config


def _dl():
    cfg = Config()
    cfg.download.pipeline = True
    cfg.ui.mode = "advanced"
    dl = VKMediaDownloader(cfg)
    # silence console
    dl.console = MagicMock()
    dl.console.is_normal.return_value = False
    dl.console.begin_video = MagicMock()
    dl.console.item_status = MagicMock()
    dl.console.done_box = MagicMock()
    return dl


def test_pipeline_producer_consumer_success(tmp_path):
    dl = _dl()
    fake_data = {
        "title": "vid",
        "mpd_text": "<MPD></MPD>",
        "mpd_url": "https://cdn/manifest.mpd",
        "user_agent": "UA",
        "cookies": [],
        "video_segment_url": None,
        "audio_segment_url": None,
        "video_segment_base": None,
        "audio_segment_base": None,
    }
    # mock browser get_mpd to return fake_data
    dl.browser_helper.get_mpd = AsyncMock(return_value=fake_data)
    # mock _download_with_retry to return DONE
    async def fake_retry(data, url, vq, aq, fmt, idx, folder, browser=None, **_kw):
        return {"index": idx, "url": url, "title": f"title{idx}", "folder": folder or "", "status": "DONE", "reason": ""}

    dl._download_with_retry = fake_retry
    outcomes = {}
    links = ["https://vkvideo.ru/video-1_1", "https://vkvideo.ru/video-1_2"]

    async def run():
        await dl._run_batch_pipeline(MagicMock(), None, links, outcomes, "best", "best", "mkv")

    asyncio.run(run())
    assert len(outcomes) == 2
    assert all(v["status"] == "DONE" for v in outcomes.values())


def test_pipeline_producer_error_becomes_outcome():
    dl = _dl()
    # first link succeeds, second raises in get_mpd
    fake_data = {
        "title": "vid",
        "mpd_text": "<MPD></MPD>",
        "mpd_url": "https://cdn/manifest.mpd",
        "user_agent": "UA",
        "cookies": [],
    }

    async def get_mpd_side(browser, url):
        if "1_2" in url:
            raise RuntimeError("browser fail")
        return fake_data

    dl.browser_helper.get_mpd = AsyncMock(side_effect=get_mpd_side)
    dl._download_with_retry = AsyncMock(
        return_value={"index": 1, "url": "x", "title": "t", "folder": "", "status": "DONE", "reason": ""}
    )
    outcomes = {}
    links = ["https://vkvideo.ru/video-1_1", "https://vkvideo.ru/video-1_2"]

    asyncio.run(dl._run_batch_pipeline(MagicMock(), None, links, outcomes, "best", "best", "mkv"))
    assert len(outcomes) == 2
    # second should be ERROR from producer (status ERROR)
    err = [v for v in outcomes.values() if v["status"] == "ERROR"]
    assert len(err) == 1
    assert "browser fail" in err[0]["reason"]


def test_pipeline_stale_mpd_retry_uses_same_data(monkeypatch):
    # dict-сессия без browser — реюз старой data (backward compat)
    dl = _dl()
    dl.config.download.auto_retry_delay = 0
    dl.config.download.auto_retries = 1
    call_count = {"n": 0}
    fake_data = {
        "title": "vid",
        "mpd_text": "<MPD></MPD>",
        "mpd_url": "https://cdn/manifest.mpd",
        "user_agent": "UA",
        "cookies": [],
    }

    async def fake_mat(data, url, vq, aq, fmt, idx, folder):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    dl._materialize = fake_mat
    outcomes = {}
    import asyncio

    async def test_retry():
        res = await dl._download_with_retry(fake_data, "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None)
        return res

    res = asyncio.run(test_retry())
    assert res["status"] == "DONE"
    assert call_count["n"] == 2


def test_pipeline_stale_mpd_refreshes_with_browser(monkeypatch):
    # MediaSession + browser + auth-ошибка → должен рефрешить MPD
    from vk_downloader.core.session import MediaSession

    dl = _dl()
    dl.config.download.auto_retry_delay = 0
    dl.config.download.auto_retries = 1
    session = MediaSession(
        url="https://vkvideo.ru/video-1_1",
        mpd_url="https://cdn/manifest.mpd",
        mpd_text="<MPD></MPD>",
        title="vid",
        user_agent="UA",
        cookies=[],
        ttl=0,  # сразу stale
    )
    refreshed = MediaSession(
        url="https://vkvideo.ru/video-1_1",
        mpd_url="https://cdn/manifest2.mpd",
        mpd_text="<MPD>new</MPD>",
        title="vid2",
        user_agent="UA",
        cookies=[],
    )
    dl.browser_helper.get_mpd = AsyncMock(return_value=refreshed)
    call_data = {}

    async def fake_mat(data, url, vq, aq, fmt, idx, folder):
        call_data["url"] = getattr(data, "mpd_url", data.get("mpd_url"))
        if call_data.get("n", 0) == 0:
            call_data["n"] = 1
            raise RuntimeError("401 Unauthorized - token expired")
        call_data["n"] = 2
        return "ok"

    dl._materialize = fake_mat
    import asyncio

    async def test_retry():
        return await dl._download_with_retry(
            session, "https://vkvideo.ru/video-1_1", "best", "best", "mkv", 1, None, browser=MagicMock()
        )

    res = asyncio.run(test_retry())
    assert res["status"] == "DONE"
    assert dl.browser_helper.get_mpd.call_count == 1
    assert call_data["url"] == "https://cdn/manifest2.mpd"
