"""Консольный интерфейс: два режима вывода — normal и advanced.

normal   — упрощённый вывод: баннер, блок ENVIROMENT, строки
           ``[NN] Название   XXX% - [СТАТУС]`` с агрегированным процентом
           всех стадий (видео + аудио + склейка), общий PROGRESS и финальный
           бокс SUCCESS/FAILURE. Технический шум подавляется автоматически.
advanced — полный технический вывод (--advanced/--mode advanced/--debug):
           секции MPD/DOWNLOAD, CDN-детали, прогресс-бары с числом воркеров.
"""

import sys
import threading
import time
from pathlib import Path

_ART = r"""
██╗   ██╗  ██╗  ██╗  ██╗   ██╗  ██████╗
██║   ██║  ██║ ██╔╝  ██║   ██║  ██╔══██╗
██║   ██║  █████╔╝   ██║   ██║  ██║  ██║
╚██╗ ██╔╝  ██╔═██╗   ╚██╗ ██╔╝  ██║  ██║
 ╚████╔╝   ██║  ██╗   ╚████╔╝   ██████╔╝
  ╚═══╝    ╚═╝  ╚═╝    ╚═══╝    ╚═════╝"""

_SUBTITLE = "V K   V I D E O   D O W N L O A D E R"
_ART_WIDTH = max(len(line) for line in _ART.splitlines())
BANNER = "\n" + _ART + "\n" + _SUBTITLE.center(_ART_WIDTH) + "\n"

_TITLE_WIDTH = 40
# Вес стадии в агрегированном проценте видео
_STAGE_WEIGHTS = {"video": 0.45, "audio": 0.45, "merge": 0.10}


class Console:
    """КОНСОЛЬНЫЙ ИНТЕРФЕЙС: РЕЖИМЫ NORMAL/ADVANCED, СПИННЕР, МЕНЮ"""

    def __init__(self, config):
        self.config = config
        self.mode = getattr(config.ui, "mode", "advanced")
        self._log = None
        self._spin_stop = threading.Event()
        self._spin_thread = None
        self._spin_text = ""
        self._progress_width = 0
        self._video_index = 0
        self._video_title = ""
        self._stages: dict[str, float] = {}
        self._status = ""
        self._frame_i = 0
        self._ticker: threading.Thread | None = None
        self._tick_on = threading.Event()
        if config.logs.save_log:
            config.logs.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log = config.logs.log_file.open("w", encoding="utf-8")
            self.log(f"=== LOG [{config.logs.log_id}] ===")

    # ------------------------------------------------------------------
    # Базовый вывод
    # ------------------------------------------------------------------

    # Печать независимо от режима: критичные сообщения и интерактив
    def _always(self, message: str = "", end: str = "\n") -> None:
        self.spin_stop()
        sys.stdout.write(str(message) + end)
        sys.stdout.flush()
        self.log(message)

    # Обычный вывод: только в advanced (в normal уходит лишь в лог)
    def write(self, message: str = "") -> None:
        message = str(message)
        if not self.is_normal():
            self.spin_stop()
            print(message, flush=True)
        self.log(message)

    # Запись в лог без вывода в консоль
    def log(self, message: str = "") -> None:
        if self._log:
            self._log.write(f"{message}\n")
            self._log.flush()

    # Заголовок секции вида ─────── MPD ─────── с отступом сверху
    def section(self, title: str) -> None:
        width = self.config.ui.section_width
        pad = max((width - len(title)) // 2 - 1, 0)
        line = "─" * pad + f" {title} " + "─" * max(width - pad - len(title) - 2, 0)
        self.write("")
        self.write(line)

    # Заголовок для normal-режима
    def normal_header(self, title: str) -> None:
        if not self.is_normal():
            return
        width = self.config.ui.section_width
        pad = max((width - len(title)) // 2 - 1, 0)
        line = "─" * pad + f" {title} " + "─" * max(width - pad - len(title) - 2, 0)
        self._always("")
        self._always(line)

    # Статусная строка "* Status: ..." (advanced)
    def status(self, text: str, ok: bool = True) -> None:
        mark = "✓" if ok else "✗"
        self.write(f"* {mark} {text}")

    # Строка проверок "* Label<pad> ✓ значение" (оба режима)
    def status_line(self, label: str, ok: bool, value: str = "") -> None:
        mark = "✓" if ok else "✗"
        suffix = f" {value}" if value else ""
        self._always(f"* {label:<18} {mark}{suffix}")

    # Статус обработки файла: DONE / SKIP / ERROR / RETRY (advanced)
    def item_status(self, index: int, title: str, status: str, extra: str = "") -> None:
        marks = {"DONE": "✓", "SKIP": "»", "ERROR": "✗", "RETRY": "↻"}
        mark = marks.get(status, "•")
        suffix = f" ({extra})" if extra else ""
        self.write(f"* {mark} [{index:02d}] {title} — {status}{suffix}")

    # Сводная таблица результатов по всем файлам (advanced)
    def summary(self, results: list[dict]) -> None:
        self.section("SUMMARY")
        counts = {}
        for item in results:
            status = item["status"]
            counts[status] = counts.get(status, 0) + 1
            extra = f" | {item['reason']}" if item.get("reason") else ""
            folder = f"[{item['folder']}] " if item.get("folder") else ""
            self.write(f"[{item['index']:02d}] {status:<6} {folder}{item['title']}{extra}")
        stats = " | ".join(f"{key}: {value}" for key, value in counts.items())
        self.write("")
        self.write(f"Total: {len(results)} | {stats}")

    # Запуск индикатора загрузки (только advanced)
    def spin_start(self, text: str) -> None:
        if self.is_normal():
            return
        self.spin_stop()
        self._spin_text, self._spin_i = text, 0
        self._spin_stop.clear()
        self._spin_thread = threading.Thread(target=self._spin_loop, daemon=True)
        self._spin_thread.start()

    # Остановка индикатора загрузки
    def spin_stop(self) -> None:
        if not self._spin_thread:
            return
        self._spin_stop.set()
        self._spin_thread.join()
        self._spin_thread = None
        sys.stdout.write("\r" + " " * (len(self._spin_text) + 4) + "\r")
        sys.stdout.flush()

    # Цикл анимации индикатора
    def _spin_loop(self) -> None:
        frames = self.config.ui.spin_frames
        while not self._spin_stop.is_set():
            frame = frames[self._spin_i % len(frames)]
            sys.stdout.write(f"\r{frame} {self._spin_text}")
            sys.stdout.flush()
            self._spin_i += 1
            time.sleep(self.config.ui.spin_interval)

    # Интерактивный выбор качества (работает в обоих режимах)
    def select_quality(self, kind: str, tracks: list[dict]) -> dict:
        label = f"Available {kind} quality:"
        options = []
        for track in tracks:
            codec = track.get("codecs") or track.get("mime") or "unknown"
            if kind == "video":
                options.append(f"{track['height']}p / {codec} / {track['bandwidth'] // 1000} kbps")
            else:
                options.append(f"{track['bandwidth'] // 1000} kbps / {codec}")
        self._always("")
        self._always(label)
        for index, option in enumerate(options):
            self._always(f"[{index}] - {option}")
        while True:
            raw = input("Select Num: ").strip()
            if raw == "":
                return tracks[0]
            if raw.isdigit() and int(raw) < len(tracks):
                return tracks[int(raw)]

    # Меню ретрая после прохода всех ссылок (работает в обоих режимах)
    def ask_retry(self, failed: int, skipped: int) -> bool:
        self._always("")
        self._always(f"Failed links: {failed} | Skipped links: {skipped}")
        self._always("[0] - retry failed links")
        self._always("[1] - exit")
        return input("Select Num: ").strip() == "0"

    # Блок информации о медиа (advanced)
    def media_info(self, video: dict | None, audio: dict | None, output_format: str) -> None:
        self.write("")
        self.write("+================================+")
        self.write("|         [ MEDIA INFO ]         |")
        self.write("+================================+")
        if video:
            codec = video.get("codecs") or video.get("mime") or "unknown"
            width, height = video.get("width", 0), video.get("height", 0)
            kbps = video.get("bandwidth", 0) // 1000
            self.write(f"Video   : {codec} / {width}x{height} / {kbps} kbps")
        else:
            self.write("Video   : (disabled)")
        if audio:
            codec = audio.get("codecs") or audio.get("mime") or "unknown"
            kbps = audio.get("bandwidth", 0) // 1000
            self.write(f"Audio   : {codec} / audio / {kbps} kbps")
        else:
            self.write("Audio   : (disabled)")
        out_label = output_format
        target_audio = getattr(self.config.media, "audio_target_format", None)
        if target_audio and output_format in self.config.media.video_formats:
            out_label = f"{output_format} (audio: {target_audio})"
        self.write(f"Output  : {out_label}")

    # Прогресс: в normal агрегируется по стадиям, в advanced — бар
    def progress(
        self,
        label: str,
        current: float,
        total: float,
        started: float,
        workers: int | None = None,
    ) -> None:
        total = max(total, 1.0)
        percent = min(max(current / total * 100, 0.0), 100.0)
        if self.is_normal():
            if label.startswith("Merged"):
                stage = "merge"
            elif label.startswith("Audio"):
                stage = "audio"
            else:
                stage = "video"
            self.stage_update(stage, percent / 100.0)
            return
        width = self.config.ui.bar_width
        filled = int(width * min(percent, 100.0) / 100)
        bar = "█" * filled + "░" * (width - filled)
        speed = current / max(time.time() - started, 0.001)
        extra = f" | {speed:.1f}/s"
        if workers is not None:
            extra += f" | workers {workers}"
        line = f"{bar} {percent:6.2f}%{extra}"
        if len(line) < self._progress_width:
            line = line.ljust(self._progress_width)
        self._progress_width = len(line)
        print("\r" + line, end="", flush=True)
        if percent >= 100.0:
            print(flush=True)
            self._progress_width = 0

    # Путь к результатам (печатается перед финальным боксом)
    def output_folder(self, output_dir: Path) -> None:
        self._always("")
        self._always(f"Output Folder: {output_dir.resolve()}")

    # Финальный бокс: SUCCESS когда всё скачано, FAILURE при наличии проблем
    def final_box(self, all_ok: bool = True) -> None:
        if all_ok:
            self._always("╔═════════════════════════╗")
            self._always("║       S U C C E S S     ║")
            self._always("╚═════════════════════════╝")
        else:
            self._always("╔═════════════════════════╗")
            self._always("║       F A I L U R E     ║")
            self._always("╚═════════════════════════╝")

    # Рамка после каждого успешно скачанного видео (advanced)
    def done_box(self) -> None:
        self.write("")
        self.write("╔═════════════════════╗")
        self.write("║         DONE        ║")
        self.write("╚═════════════════════╝")

    # Блок ошибки
    def error(self, message: str) -> None:
        self._always("")
        self._always("╔═════════════════════╗")
        self._always("║        ERROR        ║")
        self._always("╚═════════════════════╝")
        self._always(f"Reason: {message}")

    # Закрытие лог-файла
    def close(self) -> None:
        self.tick_stop()
        self.spin_stop()
        if self._log:
            self._log.close()
            self._log = None

    # ------------------------------------------------------------------
    # Normal mode: упрощённый дружелюбный вывод
    # ------------------------------------------------------------------

    def is_normal(self) -> bool:
        return self.mode == "normal"

    # Большая приветственная надпись
    def banner(self) -> None:
        if not self.is_normal():
            return
        self._always(BANNER)

    # Начало работы над плейлистом
    def begin_playlist(self, title: str, count: int | None = None) -> None:
        if not self.is_normal():
            return
        name = title or "(без названия)"
        suffix = f" ({count} videos)" if count is not None else ""
        self._always("")
        self._always(f"Playlist: {name}{suffix}")

    # Начало обработки одного видео
    def begin_video(self, index: int, title: str) -> None:
        if not self.is_normal():
            return
        self._video_index = index
        self._video_title = title
        self._stages = {}
        self._status = ""

    # Обновление заголовка текущего видео (после захвата страницы)
    def video_title(self, title: str) -> None:
        if self.is_normal() and title:
            self._video_title = title
            self._redraw()

    # Фаза сканирования манифеста: live-строка с вращающимся кадром
    def scan_start(self) -> None:
        if not self.is_normal():
            return
        self._status = "MPD"
        self._redraw()
        self._tick_on.set()
        if not self._ticker:
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self._ticker.start()

    # Переход к загрузке: фиксируем набор стадий, чтобы общий процент
    # был монотонным (без сбросов при переключении видео->аудио->склейка)
    def loading_start(self, stages: list[str]) -> None:
        if not self.is_normal():
            return
        self.tick_stop()
        self._stages = dict.fromkeys(stages, 0.0)
        self._status = "LOADING"
        self._redraw()

    # Обновление доли стадии; процент видео = сумма весов затронутых стадий
    def stage_update(self, stage: str, fraction: float) -> None:
        if not self.is_normal():
            return
        self._stages[stage] = min(max(fraction, 0.0), 1.0)
        self._status = "LOADING"
        self._redraw()

    # Агрегированный процент по задействованным стадиям
    def _aggregated(self) -> tuple[int, bool]:
        if not self._stages:
            return 0, False
        weight_sum = sum(_STAGE_WEIGHTS[s] for s in self._stages)
        value = sum(_STAGE_WEIGHTS[s] * f for s, f in self._stages.items()) / max(weight_sum, 1e-9)
        return int(value * 100), True

    # Перерисовка live-строки — спин берём из config.ui.spin_frames (единственный источник)
    def _redraw(self) -> None:
        title = self._video_title[:_TITLE_WIDTH].ljust(_TITLE_WIDTH)
        index = f"[{self._video_index:02d}] "
        if self._status == "MPD":
            frames = self.config.ui.spin_frames
            frame = frames[self._frame_i % len(frames)]
            body = f"{index}{title}  {frame:^5} - [MPD]"
        else:
            pct, _ = self._aggregated()
            body = f"{index}{title}  {pct:03d}% - [{self._status or 'WAIT'}]"
        sys.stdout.write("\r" + body + "   ")
        sys.stdout.flush()

    # Фоновое вращение кадра для фазы MPD
    def _tick_loop(self) -> None:
        while self._tick_on.is_set():
            self._frame_i += 1
            if self._status == "MPD":
                self._redraw()
            time.sleep(0.12)

    # Остановка тикера кадров
    def tick_stop(self) -> None:
        self._tick_on.clear()
        if self._ticker:
            self._ticker.join()
            self._ticker = None

    # Событие: фиксирует строку со статусом и переводит каретку
    def video_event(self, status: str, reason: str = "") -> None:
        if not self.is_normal():
            return
        self.tick_stop()
        # RETRY показываем без старого прогресса, чтобы 100% не вводил в заблуждение
        if status == "RETRY":
            self._stages = {}
            self._status = ""
        title = self._video_title[:_TITLE_WIDTH].ljust(_TITLE_WIDTH)
        note = f" ({reason})" if reason else ""
        pct, has_pct = self._aggregated()
        middle = f"{pct:03d}%" if has_pct else "  — "
        sys.stdout.write(f"\r[{self._video_index:02d}] {title}  {middle} - [{status}]{note}   \n")
        sys.stdout.flush()
        self._stages = {}
        self._status = ""

    # Завершение видео успехом
    def video_done(self) -> None:
        if not self.is_normal():
            return
        self.tick_stop()
        self._stages.update({"video": 1.0, "audio": 1.0, "merge": 1.0})
        title = self._video_title[:_TITLE_WIDTH].ljust(_TITLE_WIDTH)
        sys.stdout.write(f"\r[{self._video_index:02d}] {title}  100% - [DONE]   \n")
        sys.stdout.flush()
        self._stages = {}
        self._status = ""

    # Общий прогресс внизу после каждого видео
    def footer(self, videos_done: int, videos_total: int) -> None:
        if not self.is_normal():
            return
        self._always("")
        self._always(f"{'─' * 4} PROGRESS: {videos_done:03d}/{videos_total:03d} VIDEOS " + "─" * 8)
        self._always("")


# === Пример ===
# from vk_downloader.settings import Config
# from vk_downloader.ui.console import Console
# console = Console(Config())
# console.section("MPD")
# console.status("Successful connection")
