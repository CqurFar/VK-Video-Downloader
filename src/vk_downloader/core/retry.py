"""Централизованная retry-политика: что ретраить, а что нет."""

from __future__ import annotations

from dataclasses import dataclass

from vk_downloader.core.errors import (
    FFmpegMergeError,
    FFmpegNotFoundError,
    FirefoxProfileNotFoundError,
    GeckodriverNotFoundError,
    PlaylistExtractionError,
    QualityNotAvailableError,
)

# Программистские ошибки — никогда не ретраим автоматически
PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    AssertionError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    NameError,
    ImportError,
    NotImplementedError,
    SyntaxError,
)

# Детерминированные ошибки VK — повтор не поможет
NON_RETRYABLE_VK: tuple[type[BaseException], ...] = (
    QualityNotAvailableError,
    FFmpegNotFoundError,
    FirefoxProfileNotFoundError,
    GeckodriverNotFoundError,
    PlaylistExtractionError,
    FFmpegMergeError,
)


def is_retryable(exc: BaseException) -> bool:
    """True если ошибку имеет смысл ретраить (транзиентная), иначе False.

    Программистские ошибки и детерминированные VK-ошибки не ретраятся —
    они сразу превращаются в итоговый ERROR/SKIP без ожидания.
    """
    if isinstance(exc, PROGRAMMING_ERRORS):
        return False
    if isinstance(exc, NON_RETRYABLE_VK):
        return False
    # MPDNotFoundError — особый случай: в оркестраторе это SKIP, а не ретрай
    # самого сегмента, но на уровне файла его всё равно не стоит ретраить бесконечно.
    # Оставляем его retryable=False чтобы _process_with_retry быстро вернул SKIP.
    from vk_downloader.core.errors import MPDNotFoundError

    return not isinstance(exc, MPDNotFoundError)


@dataclass(frozen=True)
class RetryPolicy:
    """Явная политика повторов — один источник истины вместо размазанных констант."""

    request_attempts: int = 3  # _download_resource
    segment_passes: int = 3  # max_passes в DashDownloader
    rescue_passes: int = 2
    file_attempts: int = 3  # auto_retries на уровне файла
    base_delay: float = 3.0  # auto_retry_delay
    rescue_delay: float = 5.0

    @classmethod
    def from_config(cls, config) -> RetryPolicy:
        return cls(
            request_attempts=config.download.retries,
            segment_passes=config.download.max_passes,
            rescue_passes=config.download.rescue_passes,
            file_attempts=config.download.auto_retries,
            base_delay=float(config.download.auto_retry_delay),
            rescue_delay=float(config.download.rescue_delay),
        )

    def file_delay(self, attempt: int) -> float:
        """Линейный backoff: delay * (attempt+1)."""
        return self.base_delay * (attempt + 1)
