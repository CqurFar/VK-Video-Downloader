"""Тесты иерархии предметных исключений."""

import pytest

from vk_downloader.core.errors import (
    VKDownloadError,
    PlayerNotFoundError,
    MarionetteDisabledError,
    MPDNotFoundError,
)


def test_hierarchy():
    assert issubclass(MarionetteDisabledError, PlayerNotFoundError)
    assert issubclass(PlayerNotFoundError, VKDownloadError)
    assert issubclass(MPDNotFoundError, VKDownloadError)


def test_raise_caught_as_base():
    with pytest.raises(VKDownloadError):
        raise MPDNotFoundError("missing")
