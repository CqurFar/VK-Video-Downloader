"""Тесты централизованной retry-политики."""

import pytest

from vk_downloader.core.errors import (
    FFmpegMergeError,
    QualityNotAvailableError,
    MPDNotFoundError,
)
from vk_downloader.core.retry import RetryPolicy, is_retryable
from vk_downloader.settings import Config


def test_is_retryable_transient_true():
    assert is_retryable(RuntimeError("cdn timeout")) is True
    assert is_retryable(ConnectionError("reset")) is True
    assert is_retryable(OSError("network")) is True


def test_is_retryable_programming_false():
    assert is_retryable(TypeError("bad type")) is False
    assert is_retryable(AttributeError("no attr")) is False
    assert is_retryable(KeyError("missing")) is False
    assert is_retryable(AssertionError("assert")) is False
    assert is_retryable(NameError("name")) is False


def test_is_retryable_deterministic_vk_false():
    assert is_retryable(QualityNotAvailableError("no 1080")) is False
    assert is_retryable(FFmpegMergeError("ffmpeg fail")) is False
    assert is_retryable(MPDNotFoundError("not found")) is False


def test_retry_policy_from_config():
    cfg = Config()
    cfg.download.auto_retries = 5
    cfg.download.auto_retry_delay = 7
    cfg.download.retries = 4
    cfg.download.max_passes = 2
    cfg.download.rescue_passes = 1
    cfg.download.rescue_delay = 9
    policy = RetryPolicy.from_config(cfg)
    assert policy.file_attempts == 5
    assert policy.base_delay == 7
    assert policy.request_attempts == 4
    assert policy.segment_passes == 2
    assert policy.rescue_passes == 1
    assert policy.rescue_delay == 9


def test_retry_policy_file_delay_linear():
    p = RetryPolicy(base_delay=3.0)
    assert p.file_delay(0) == 3.0
    assert p.file_delay(1) == 6.0
    assert p.file_delay(2) == 9.0
