"""Предметные исключения загрузчика.

Все ошибки наследуются от :class:`VKDownloadError`, что позволяет верхнему
уровню отличать ожидаемые ситуации работы пайплайна от неожиданных сбоев.

Семантика для оркестратора (см. ``core.downloader._process_with_retry``):
- ``MPDNotFoundError``  — видео недоступно/плеер не стартовал -> статус SKIP,
  ссылка попадает в failed.txt без признака фатальности;
- остальные ``VKDownloadError`` — статус ERROR с автоматическими повторами.
"""

from __future__ import annotations


class VKDownloadError(Exception):
    """Базовая ошибка пайплайна: все предметные исключения наследуют её."""


class PlayerNotFoundError(VKDownloadError):
    """Страница плеера не открылась или вкладка была потеряна браузером."""


class MarionetteDisabledError(PlayerNotFoundError):
    """Firefox запущен без флага ``-marionette``: WebDriver не может подключиться."""


class GeckodriverNotFoundError(VKDownloadError):
    """Исполняемый файл geckodriver не найден ни в PATH, ни в packages/."""


class FirefoxProfileNotFoundError(VKDownloadError):
    """Явно заданный профиль Firefox (VK_DOWNLOADER_FIREFOX_PROFILE) не существует.

    Поднимается только если путь указан пользователем вручную и не найден:
    это однозначная ошибка конфигурации. Дефолтный отсутствующий профиль —
    лишь предупреждение, т.к. мы подключаемся к уже запущенному браузеру.
    """


class WebDriverError(VKDownloadError):
    """Ошибка транспорта к geckodriver: HTTP-статус, таймаут, отказ соединения."""


class MPDNotFoundError(VKDownloadError):
    """Манифест DASH не обнаружен в трафике за отведённое время.

    Отдельный класс нужен оркестратору: такая ситуация трактуется как SKIP
    (видео приватное/удалённое), а не как технический сбой.
    """


class QualityNotAvailableError(VKDownloadError):
    """Запрошенное качество или кодек отсутствует среди треков манифеста."""


class PlaylistExtractionError(VKDownloadError):
    """Не удалось извлечь ссылки со страницы плейлиста (пусто/приватно)."""


class FFmpegNotFoundError(VKDownloadError):
    """ffmpeg не найден ни в packages/, ни в PATH."""


class FFmpegMergeError(VKDownloadError):
    """ffmpeg завершился с ошибкой при склейке дорожек."""


class SegmentDownloadError(VKDownloadError):
    """Часть сегментов не скачалась после всех повторов и спасательной фазы."""
