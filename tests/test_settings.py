"""Тесты конфигурации приложения."""

import vk_downloader
from vk_downloader.settings import Config, DownloadConfig


def test_config_defaults():
    cfg = Config()
    assert cfg.browser.marionette_port == 2828
    assert cfg.browser.geckodriver_port == 4444
    assert cfg.download.workers_max == 64
    assert cfg.download.workers_min == 8
    assert cfg.media.default_format == "mkv"


def test_initial_workers_tiers():
    dl = DownloadConfig()
    assert dl.initial_workers(1) == 1
    assert dl.initial_workers(50) == 16
    assert dl.initial_workers(10_000) == 64


def test_apply_debug_changes_timeouts():
    cfg = Config()
    cfg.apply_debug()
    assert cfg.debug is True
    assert cfg.browser.mpd_timeout == 20
    assert cfg.download.request_timeout == 30


def test_version_exported():
    assert vk_downloader.__version__ == "2.1.0"
