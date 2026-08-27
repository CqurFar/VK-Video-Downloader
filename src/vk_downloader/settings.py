"""Конфигурация приложения: пути, браузер, загрузка, медиа, пакеты."""

import os
import random
import re
import string
from pathlib import Path


class PathConfig:
    """ПУТИ"""

    def __init__(self):
        self.output_dir = Path("./downloaded")
        self.logs_dir = Path("./downloaded/logs")
        self.temp_name = ".temp_m4s"

        self.urls_file = Path("./urls.txt")
        self.failed_file = Path("./failed.txt")
        self.packages_dir = Path("./packages")
        self.ffmpeg_dir = Path("./packages/ffmpeg")
        self.geckodriver_path = Path("./packages/geckodriver/geckodriver.exe")

        env_profile = os.environ.get("VK_DOWNLOADER_FIREFOX_PROFILE", "")
        self.browser_profile_explicit = bool(env_profile)
        self.browser_profile = Path(env_profile or "./packages/firefox_profile")

    # Папка
    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


class LogConfig:
    """ЛОГИРОВАНИЕ"""

    def __init__(self, paths: PathConfig):
        self.save_log = False
        self.save_mpd = False
        self.log_id_length = 6
        self.log_id = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=self.log_id_length)
        )
        self.log_file = paths.logs_dir / f"log_[{self.log_id}].txt"


class BrowserConfig:
    """FIREFOX И WEBDRIVER"""

    def __init__(self):
        self.name = "firefox"

        self.marionette_port = 2828  # порт Marionette в Firefox
        self.geckodriver_port = 4444  # HTTP-порт geckodriver
        self.connect_timeout = 5  # ожидание поднятия geckodriver, сек
        self.mpd_timeout = 10  # сетевой таймаут загрузки MPD, сек
        self.mpd_collect_timeout = 30  # общее ожидание MPD-манифеста, сек
        self.player_retry_delay = 2  # пауза между попытками запуска плеера
        self.script_timeout = 30  # таймаут выполнения JS в браузере
        self.playlist_script_timeout = 120  # таймаут JS извлечения плейлиста
        self.title_wait_seconds = 5  # ожидание появления названия видео
        self.player_start_delay = 2  # пауза перед активацией плеера
        self.traffic_capture_seconds = 12  # минимальное время сбора трафика
        self.traffic_capture_timeout = 40  # максимальное время сбора трафика
        self.context_attempts = 3  # повторы при потере вкладки браузера
        self.session_id = ""  # явный id WebDriver-сессии для from_existing (""=авто)


class DownloadConfig:
    """ЗАГРУЗКА СЕГМЕНТОВ"""

    def __init__(self):
        self.workers_max = 64
        self.workers_min = 8
        self.retries = 3
        self.request_timeout = 15
        self.stall_timeout = 30  # перезапуск, если сегмент не растёт за N сек

        self.max_passes = 3  # проходов по сегменту до "безнадёжных"
        self.rescue_delay = 5  # пауза перед спасательной фазой, сек
        self.rescue_passes = 2  # проходов в спасательной фазе

        self.tail_ratio = 0.9  # доля скачанного для заморозки workers
        self.tail_min_segments = 100  # минимальный трек для заморозки

        self.auto_retries = 3  # авто-попыток на файл до ручного ретрая
        self.auto_retry_delay = 3  # базовая пауза между авто-попытками
        self.pipeline = False  # параллельный захват MPD при загрузке (--pipeline)

        # Порог числа сегментов (начальные workers)
        self.worker_tiers = (
            (2, 1),
            (25, 8),
            (100, 16),
            (500, 32),
            (2000, 48),
            (None, 64),
        )

    # Стартовое число worker по количеству сегментов
    def initial_workers(self, segment_count: int) -> int:
        for threshold, workers in self.worker_tiers:
            if threshold is None or segment_count < threshold:
                return min(workers, self.workers_max)
        return self.workers_max


class BalanceConfig:
    """БАЛАНСИРОВЩИК WORKERS"""

    def __init__(self):
        self.grow_after = 16  # успешных задач до роста числа worker
        self.step = 8  # шаг изменения числа worker


class MediaConfig:
    """МЕДИА, ФОРМАТЫ И КОДЕКИ"""

    def __init__(self):
        self.video_formats = {"mp4", "mkv", "webm"}
        self.audio_formats = {"mp3", "aac", "m4a", "ogg", "opus", "flac", "wav"}

        # Семейства кодеков: сравниваются с первым компонентом fourcc из MPD
        self.webm_video = ("vp8", "vp9", "vp09", "av01")
        self.webm_audio = ("opus", "vorbis")
        self.mp4_video = ("avc1", "avc3", "hvc1", "hev1", "av01")
        self.mp4_audio = ("mp4a",)
        # MKV допускает practically любые кодеки: не режем VP9/AV1 как для mp4
        self.mkv_video = ("avc1", "avc3", "hvc1", "hev1", "av01", "vp8", "vp9", "vp09")
        self.mkv_audio = ("mp4a", "aac", "opus", "vorbis", "flac", "mp3", "eac3", "ac-3", "ec-3")

        self.default_video = "best"
        self.default_audio = "best"
        self.default_format = "mkv"

        # --format VIDEO+AUDIO: аудиокодек внутри контейнера (напр. mkv+aac)
        self.audio_target_format: str | None = None
        # --ffmpeg: дополнительные аргументы, дописываются в конец команды ffmpeg
        self.ffmpeg_extra_args: list[str] = []


class NamingConfig:
    """ИМЕНА ФАЙЛОВ"""

    def __init__(self):
        self.safe_name_re = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
        self.max_name = 80  # лимит длины имени (Windows)
        self.fallback_name = "vk-video"


class UIConfig:
    """КОНСОЛЬНЫЙ ИНТЕРФЕЙС"""

    def __init__(self):
        self.spin_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spin_interval = 0.1
        self.section_width = 55
        self.bar_width = 36
        # normal — упрощённый вывод; advanced — подробный; --debug — advanced + логи
        self.mode = "normal"


class Config:
    """КОРНЕВОЙ КОНФИГ ПРИЛОЖЕНИЯ"""

    def __init__(self):
        self.debug = False
        self.paths = PathConfig()
        self.browser = BrowserConfig()
        self.download = DownloadConfig()
        self.balance = BalanceConfig()
        self.media = MediaConfig()
        self.naming = NamingConfig()
        self.ui = UIConfig()
        self.logs = LogConfig(self.paths)

    # Папка для логов
    def prepare(self) -> None:
        self.paths.prepare()
        if self.logs.save_log or self.logs.save_mpd:
            self.paths.logs_dir.mkdir(parents=True, exist_ok=True)

    # Профиль отладки
    def apply_debug(self) -> None:
        self.debug = True
        self.logs.save_log = True
        self.logs.save_mpd = True
        self.download.request_timeout = 30
        self.browser.mpd_timeout = 20
