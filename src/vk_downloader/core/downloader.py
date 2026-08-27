import argparse
import asyncio
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from vk_downloader.browser.media_browser import VKMediaBrowser
from vk_downloader.core import urlutils as _urlutils
from vk_downloader.core.errors import (
    FirefoxProfileNotFoundError,
    InvalidURLError,
    MPDNotFoundError,
    QualityNotAvailableError,
)
from vk_downloader.core.quality import QualitySelector
from vk_downloader.core.retry import RetryPolicy, is_retryable
from vk_downloader.core.session import MediaSession
from vk_downloader.download.dash_downloader import DashDownloader
from vk_downloader.media.ffmpeg_merger import FFmpegMerger
from vk_downloader.media.mpd_parser import MPDParser
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console
from vk_downloader.ui.fs_paths import FSPaths


class VKMediaDownloader:
    """ГЛАВНЫЙ ОРКЕСТРАТОР: ССЫЛКИ, ПЛЕЙЛИСТЫ, КАЧЕСТВО, ЗАГРУЗКА И РЕТРАЙ"""

    def __init__(self, config: Config):
        self.config = config
        self.console = Console(config)
        self.browser_helper = VKMediaBrowser(config, self.console)
        self.downloader = DashDownloader(config, self.console)
        self.merger = FFmpegMerger(config, self.console)
        self.quality = QualitySelector(config)
        self.retry_policy = RetryPolicy.from_config(config)

    # Делегировано в core/urlutils и core/quality — оставлено для совместимости
    def safe_filename(self, name: str) -> str:
        return _urlutils.safe_filename(name, self.config)

    @staticmethod
    def _short_label(url: str) -> str:
        return _urlutils.short_label(url)

    @staticmethod
    def _redact_mpd(text: str) -> str:
        return _urlutils.redact_mpd(text)

    @staticmethod
    def _filter_cookies(
        cookies: list[dict],
        *urls: str | None,
    ) -> list[dict]:
        return _urlutils.filter_cookies(cookies, *urls)

    @staticmethod
    def _codec_is(track_codecs: str, families: tuple[str, ...]) -> bool:
        return _urlutils.codec_is(track_codecs, families)

    def choose_video(self, videos: list[dict], target: int | str, output_format: str) -> dict:
        return self.quality.choose_video(videos, target, output_format)

    def choose_audio(self, audios: list[dict], target: int | str, output_format: str) -> dict:
        return self.quality.choose_audio(audios, target, output_format)

    def resolve_quality(
        self, kind: str, tracks: list[dict], requested: str, output_format: str
    ) -> dict | None:
        return self.quality.resolve_quality(kind, tracks, requested, output_format)

    @staticmethod
    def _pick(kind: str, tracks: list[dict], requested: str) -> dict:
        return QualitySelector._pick(kind, tracks, requested)

    # Обработка одной ссылки от MPD до готового файла; возвращает название
    async def process_one(
        self,
        browser,
        url: str,
        video_quality: str,
        audio_quality: str,
        output_format: str,
        index: int,
        folder: str | None = None,
    ) -> str:
        self.console.write("")
        self.console.write("Opening VK embed...")
        try:
            url = _urlutils.validate_input_url(url)
        except ValueError as exc:
            self.console.status(str(exc), ok=False)
            raise InvalidURLError(str(exc)) from exc
        self.console.write(f"Source Link: {url}")
        data = await self.browser_helper.get_mpd(browser, url)
        # Название становится известно только после загрузки страницы
        self.console.video_title(data.get("title") or "")
        return await self._materialize(
            data, url, video_quality, audio_quality, output_format, index, folder
        )

    # Материализация захваченных данных: парсинг, качество, загрузка, склейка
    async def _materialize(
        self,
        data: dict,
        url: str,
        video_quality: str,
        audio_quality: str,
        output_format: str,
        index: int,
        folder: str | None = None,
    ) -> str:
        videos, audios = MPDParser.parse(data["mpd_text"], data["mpd_url"])
        # none в качестве или аудиоформат вывода => видео-дорожка не нужна
        video = (
            None
            if output_format in self.config.media.audio_formats or video_quality.lower() == "none"
            else self.resolve_quality("video", videos, video_quality, output_format)
        )
        audio = (
            None
            if audio_quality.lower() == "none" and video is not None
            else self.resolve_quality("audio", audios, audio_quality, output_format)
        )
        if video is None and audio is None:
            raise QualityNotAvailableError(
                "Both tracks disabled: at least one of video/audio must be kept"
            )
        self.console.media_info(video, audio, output_format)
        stages = (["video"] if video else []) + (["audio"] if audio else []) + ["merge"]
        self.console.loading_start(stages)

        title = self.safe_filename(data["title"])
        if title.lower() in {"vk-video", "vk_video"}:
            match = re.search(r"[?&]id=(\d+)", url)
            title = f"VK-{match.group(1)}" if match else self.config.naming.fallback_name
        suffix = f"{index:02d}_[{self.config.logs.log_id}]"
        base_dir = self.config.paths.output_dir / folder if folder else self.config.paths.output_dir
        await asyncio.to_thread(lambda: base_dir.mkdir(parents=True, exist_ok=True))
        final = base_dir / f"{title}_{suffix}.{output_format}"
        mpd_file = self.config.paths.logs_dir / f"{title}_{suffix}.mpd"

        if self.config.logs.save_mpd:
            await asyncio.to_thread(
                lambda: mpd_file.write_text(self._redact_mpd(data["mpd_text"]), encoding="utf-8")
            )

        headers = DashDownloader.media_headers(
            data["user_agent"],
            url,
            data.get("video_segment_url") or data.get("audio_segment_url") or data["mpd_url"],
        )
        filtered = self._filter_cookies(
            data.get("cookies", []),
            data.get("video_segment_url"),
            data.get("audio_segment_url"),
            data["mpd_url"],
            url,
        )
        cookies = "; ".join(f"{c.get('name')}={c.get('value')}" for c in filtered)
        if cookies:
            headers["Cookie"] = cookies

        temp_dir = self.config.paths.output_dir / self.config.paths.temp_name
        await asyncio.to_thread(lambda: temp_dir.mkdir(parents=True, exist_ok=True))
        await asyncio.to_thread(FSPaths.hide, temp_dir)
        video_tmp = temp_dir / f"{title}_{suffix}.video.m4s"
        audio_tmp = temp_dir / f"{title}_{suffix}.audio.m4s"
        try:
            self.console.section("DOWNLOAD")
            self.console.write("DASH Segment Download Mode")
            self.console.write(f"Workers: max {self.config.download.workers_max}")
            v_base = data.get("video_segment_base")
            a_base = data.get("audio_segment_base")
            self.console.write(
                f"Video Segment Base: {_urlutils.redact_url(v_base) if v_base else 'fallback'}"
            )
            self.console.write(
                f"Audio Segment Base: {_urlutils.redact_url(a_base) if a_base else 'fallback'}"
            )
            if video:
                label = f"Video Track [{video['id']}]"
                self.console.write(f"{label} Downloading")
                await self.downloader.download_track(
                    video,
                    data.get("video_segment_base"),
                    data["mpd_url"],
                    headers,
                    video_tmp,
                    label,
                    temp_dir,
                )
            if audio:
                label = f"Audio Track [{audio['id']}]"
                self.console.write(f"{label} Downloading")
                await self.downloader.download_track(
                    audio,
                    data.get("audio_segment_base"),
                    data["mpd_url"],
                    headers,
                    audio_tmp,
                    label,
                    temp_dir,
                )
            self.console.write(f"Merged: {final.name}")
            await asyncio.to_thread(
                self.merger.merge,
                video_tmp if video else None,
                audio_tmp if audio else None,
                final,
                output_format,
                video,
                audio,
            )
        finally:
            await asyncio.to_thread(video_tmp.unlink, missing_ok=True)
            await asyncio.to_thread(audio_tmp.unlink, missing_ok=True)
        return title

    # Загрузка файла с автоматическими повторами перед ручным ретраем
    async def _process_with_retry(
        self,
        browser,
        url: str,
        video_quality: str,
        audio_quality: str,
        output_format: str,
        index: int,
        folder: str | None,
    ) -> dict:
        retries = self.config.download.auto_retries
        delay = self.config.download.auto_retry_delay
        status, reason = "ERROR", ""
        last_exc: BaseException | None = None
        for attempt in range(retries + 1):
            try:
                self.console.scan_start()
                title = await self.process_one(
                    browser,
                    url,
                    video_quality,
                    audio_quality,
                    output_format,
                    index,
                    folder,
                )
                return {
                    "index": index,
                    "url": url,
                    "title": title,
                    "folder": folder or "",
                    "status": "DONE",
                    "reason": "",
                }
            except MPDNotFoundError as exc:
                status, reason = "SKIP", str(exc)
                last_exc = exc
            except Exception as exc:
                status, reason = "ERROR", str(exc)
                last_exc = exc
            # Программистские/детерминированные ошибки не ретраим
            if last_exc is not None and not is_retryable(last_exc):
                break
            if attempt < retries:
                pause = delay * (attempt + 1)
                if self.console.is_normal():
                    self.console.video_event("RETRY", f"attempt {attempt + 1}/{retries}")
                else:
                    self.console.item_status(
                        index,
                        self._short_label(url),
                        "RETRY",
                        f"{attempt + 1}/{retries}, next in {pause}s | {reason[:90]}",
                    )
                await asyncio.sleep(pause)
        return {
            "index": index,
            "url": url,
            "title": self._short_label(url),
            "folder": folder or "",
            "status": status,
            "reason": reason,
        }

    # Повторные попытки только для проблемных ссылок после общего прохода
    async def _retry_failed(
        self,
        browser,
        outcomes: dict[tuple[str | None, int], dict],
        video_quality: str,
        audio_quality: str,
        output_format: str,
        extra: list[dict] | None = None,
    ) -> None:
        while True:
            problems = {
                key: outcome for key, outcome in outcomes.items() if outcome["status"] != "DONE"
            }
            if not problems:
                return
            results = list(outcomes.values())
            self.console.summary(results)
            skips = sum(1 for result in results if result["status"] == "SKIP")
            try:
                retry_answered = self.console.ask_retry(len(problems), skips)
            except (EOFError, OSError):
                # Нечего читать stdin (неинтерактивный запуск): считаем отказом
                retry_answered = False
            if not retry_answered:
                return
            for (folder, index), problem in problems.items():
                fresh = await self._process_with_retry(
                    browser,
                    problem["url"],
                    video_quality,
                    audio_quality,
                    output_format,
                    index,
                    folder or None,
                )
                outcomes[(folder, index)] = fresh
                if fresh["status"] == "DONE":
                    self.console.item_status(index, fresh["title"], "DONE")
                else:
                    self.console.item_status(
                        index,
                        fresh["title"],
                        fresh["status"],
                        fresh["reason"][:120],
                    )
            # Успешные ретраи сразу исчезают из файла проблемных ссылок
            self._save_failed(list(outcomes.values()) + list(extra or []))

    # Запись нескачанных ссылок в failed.txt; отсутствие проблем удаляет файл
    def _save_failed(self, results: list[dict]) -> None:
        failed = [result for result in results if result["status"] != "DONE"]
        if not failed:
            # Прошлый запуск мог оставить файл: чистим, чтобы не путать пользователя
            Path(self.config.paths.failed_file).unlink(missing_ok=True)
            return
        lines: list[str] = []
        for item in sorted(failed, key=lambda r: (r["folder"], r["index"])):
            reason = item["reason"].strip()
            label = f"{item['status']}: {reason}" if reason else item["status"]
            lines.append(f"# [{item['folder'] or 'root'}] {label}"[:200])
            lines.append(item["url"])
        self.config.paths.failed_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.console.write(f"Failed links saved: {self.config.paths.failed_file}")

    @staticmethod
    def normalize_url(url: str) -> str:
        return _urlutils.normalize_url(url)

    # Разбор --format: "mkv" | "aac"(только аудио) | "mkv+aac"(контейнер + аудио)
    # "acc" принимается как алиас правильного "aac"
    def _parse_format(self, parser: argparse.ArgumentParser, value: str) -> str:
        raw = value.strip().lower().replace(" ", "")
        raw = re.sub(r"\bacc\b", "aac", raw)
        parts = [p for p in raw.split("+") if p]
        audio_formats = self.config.media.audio_formats
        video_formats = self.config.media.video_formats
        if not parts or len(parts) > 2:
            parser.error(f'Bad --format "{value}": use fmt | audio-fmt | video+audio')
        container, target_audio = parts[0], None
        if len(parts) == 2:
            if container not in video_formats or parts[1] not in audio_formats:
                parser.error(f'Bad --format "{value}": expected VIDEO+AUDIO, e.g. mkv+aac')
            target_audio = parts[1]
            self.config.media.audio_target_format = target_audio
        elif container not in video_formats | audio_formats:
            parser.error(f"Unsupported format: {value}")
        return container

    # Аргументы CLI и список ссылок
    def parse_args(self):
        parser = argparse.ArgumentParser(description="VK Video downloader (Firefox only)")
        parser.add_argument("urls", nargs="*", help="VK video URL")
        parser.add_argument("--file", help="TXT file with URLs")
        parser.add_argument(
            "--playlist",
            help="VK playlist URL e.g. https://vkvideo.ru/playlist/-232462760_50",
        )
        parser.add_argument("--playlist-file", help="TXT file with playlist URLs, one per line")
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug mode (logs, mpd, timeouts)",
        )
        parser.add_argument(
            "--advanced",
            action="store_true",
            help="Detailed console output (default is simplified normal mode)",
        )
        parser.add_argument(
            "--mode",
            choices=("normal", "advanced", "debug"),
            default=None,
            help="Console mode: normal (simple) | advanced (verbose) | debug (= advanced + logs)",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=None,
            help="Max parallel segment workers (default from config: 64)",
        )
        parser.add_argument(
            "--pipeline",
            action="store_true",
            help="Advanced only: capture MPD of next video while current downloads"
            " (console output may interleave)",
        )
        parser.add_argument(
            "--ffmpeg",
            default="",
            help='Extra ffmpeg args appended to merge command, e.g. "-metadata title=X"',
        )
        parser.add_argument(
            "--video",
            default=self.config.media.default_video,
            help="best | none | height e.g. 1080",
        )
        parser.add_argument(
            "--audio",
            default=self.config.media.default_audio,
            help="best | none | kbps e.g. 128",
        )
        parser.add_argument("--format", default=self.config.media.default_format)
        args = parser.parse_args()
        urls = list(args.urls)
        if args.file:
            with open(args.file, encoding="utf-8") as file:
                urls.extend(
                    line.strip()
                    for line in file
                    if line.strip() and not line.lstrip().startswith("#")
                )
        urls = [self.normalize_url(u) for u in dict.fromkeys(urls)]
        has_source = (
            urls
            or (args.playlist and args.playlist.strip())
            or (args.playlist_file and args.playlist_file.strip())
        )
        if not has_source:
            parser.error("Provide URL, --file, --playlist or --playlist-file")
        output_format = self._parse_format(parser, args.format)
        if args.workers is not None and args.workers >= 1:
            self.config.download.workers_max = args.workers
        self.config.download.pipeline = args.pipeline
        try:
            self.config.media.ffmpeg_extra_args = shlex.split(args.ffmpeg) if args.ffmpeg else []
        except ValueError as exc:
            parser.error(f"Bad --ffmpeg quoting: {exc}")
        return (
            urls,
            args.video,
            args.audio,
            output_format,
            args.playlist or "",
            args.playlist_file or "",
            args.debug,
            args.advanced or args.mode == "advanced",
            args.debug or args.mode == "debug",
        )

    # Батчи одиночного источника: одна ссылка-плейлист или прямые ссылки
    async def _build_batches(
        self, browser, urls: list[str], playlist_url: str
    ) -> list[tuple[str | None, str | None, list[str]]]:
        if playlist_url.strip():
            title, links = await self.browser_helper.extract_playlist(browser, playlist_url.strip())
            links = [self.normalize_url(link) for link in links]
            self.console.status(f'Playlist "{title}" -> {len(links)} videos')
            await asyncio.to_thread(
                Path(self.config.paths.urls_file).write_text,
                "\n".join(links) + "\n",
                encoding="utf-8",
            )
            return [(None, title, links)]
        return [(None, None, urls)]

    # Скачивание одного батча ссылок с записью статусов в общий реестр.
    # В advanced-режиме работает пайплайн: пока видео N скачивается,
    # браузер уже захватывает MPD видео N+1 (browser остаётся последовательным)
    async def _run_batch(
        self,
        browser,
        folder: str | None,
        links: list[str],
        outcomes: dict[tuple[str | None, int], dict],
        video_quality: str,
        audio_quality: str,
        output_format: str,
    ) -> None:
        links = [self.normalize_url(link) for link in links]
        if self.config.download.pipeline and not self.console.is_normal():
            await self._run_batch_pipeline(
                browser, folder, links, outcomes, video_quality, audio_quality, output_format
            )
            return
        for index, url in enumerate(links, start=1):
            # Название до захвата неизвестно: стартуем с короткой меткой,
            # после открытия страницы консоль подставит реальное название
            self.console.begin_video(index, self._short_label(url))
            outcome = await self._process_with_retry(
                browser, url, video_quality, audio_quality, output_format, index, folder
            )
            outcomes[(folder or "", index)] = outcome
            status = outcome["status"]
            if self.console.is_normal():
                if status == "DONE":
                    self.console.video_done()
                else:
                    self.console.video_event(status, outcome["reason"][:60])
                done_count = sum(1 for o in outcomes.values() if o["status"] == "DONE")
                skipped = sum(1 for o in outcomes.values() if o["status"] == "SKIP")
                self.console.footer(done_count + skipped, len(links))
            else:
                self.console.item_status(index, outcome["title"], status)
                if status == "DONE":
                    self.console.done_box()
        self._save_failed(list(outcomes.values()))

    # Общий прогресс внизу в normal-режиме после каждого видео
    def _footer_after(self, outcomes: dict[tuple[str | None, int], dict]) -> None:
        values = list(outcomes.values())
        done = sum(1 for item in values if item["status"] == "DONE")
        failed = sum(1 for item in values if item["status"] == "ERROR")
        skipped = sum(1 for item in values if item["status"] == "SKIP")
        self.console.footer(done + skipped, done + failed)

    # Пайплайн: producer держит браузер (захват MPD), consumer качает и клеит
    async def _run_batch_pipeline(
        self,
        browser,
        folder: str | None,
        links: list[str],
        outcomes: dict[tuple[str | None, int], dict],
        video_quality: str,
        audio_quality: str,
        output_format: str,
    ) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)

        async def producer() -> None:
            for index, url in enumerate(links, start=1):
                try:
                    data = await self.browser_helper.get_mpd(browser, url)
                    await queue.put((index, url, data))
                except Exception as exc:
                    # Ошибка захвата превращается в готовый ERROR-исход
                    outcome = {
                        "index": index,
                        "url": url,
                        "title": self._short_label(url),
                        "folder": folder or "",
                        "status": "ERROR",
                        "reason": str(exc)[:200],
                    }
                    await queue.put((index, url, outcome))
            await queue.put(None)

        async def consumer() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                index, url, data = item
                if isinstance(data, dict) and data.get("status") == "ERROR":
                    outcome = data
                else:
                    outcome = await self._download_with_retry(
                        data,
                        url,
                        video_quality,
                        audio_quality,
                        output_format,
                        index,
                        folder,
                        browser=browser,
                    )
                outcomes[(folder or "", index)] = outcome
                if outcome["status"] == "DONE":
                    self.console.item_status(index, outcome["title"], "DONE")
                    self.console.done_box()
                else:
                    self.console.item_status(
                        index, outcome["title"], outcome["status"], outcome["reason"][:120]
                    )

        await asyncio.gather(producer(), consumer())

    # Материализация уже захваченных данных: парсинг -> качество -> download -> merge
    async def _download_with_retry(
        self,
        data: dict | MediaSession,
        url: str,
        video_quality: str,
        audio_quality: str,
        output_format: str,
        index: int,
        folder: str | None,
        browser=None,
    ) -> dict:
        status, reason = "ERROR", ""
        session = data
        for attempt in range(self.config.download.auto_retries + 1):
            try:
                title = await self._materialize(
                    session, url, video_quality, audio_quality, output_format, index, folder
                )
                return {
                    "index": index,
                    "url": url,
                    "title": title,
                    "folder": folder or "",
                    "status": "DONE",
                    "reason": "",
                }
            except Exception as exc:
                status, reason = "ERROR", str(exc)
                if not is_retryable(exc):
                    break
                # P0: stale session — рефрешим MPD/cookies/segment bases
                should_refresh = False
                if isinstance(session, MediaSession):
                    should_refresh = session.refresh_needed(exc) or session.is_stale
                elif isinstance(session, dict):
                    msg = str(exc).lower()
                    should_refresh = any(
                        k in msg for k in ("401", "403", "expired", "signature", "token")
                    )
                if should_refresh and browser is not None:
                    try:
                        session = await self.browser_helper.get_mpd(browser, url)
                        self.console.log(f"Refreshed MPD for retry {attempt + 1}")
                    except Exception as refresh_exc:
                        self.console.log(f"MPD refresh failed: {refresh_exc}")
                if attempt < self.config.download.auto_retries:
                    pause = self.config.download.auto_retry_delay * (attempt + 1)
                    if self.console.is_normal():
                        self.console.video_event("RETRY", f"attempt {attempt + 1}")
                    else:
                        self.console.item_status(
                            index,
                            self._short_label(url),
                            "RETRY",
                            f"{attempt + 1} | {reason[:90]}",
                        )
                    await asyncio.sleep(pause)
        return {
            "index": index,
            "url": url,
            "title": self._short_label(url),
            "folder": folder or "",
            "status": status,
            "reason": reason,
        }

    # Файл плейлистов: каждый плейлист обрабатывается целиком до перехода к следующему
    async def _run_playlist_file(
        self,
        browser,
        playlist_file: str,
        outcomes: dict[tuple[str | None, int], dict],
        video_quality: str,
        audio_quality: str,
        output_format: str,
    ) -> list[dict]:
        def _read_playlists() -> list[str]:
            with open(playlist_file.strip(), encoding="utf-8") as file:
                return [
                    line.strip()
                    for line in file
                    if line.strip() and not line.lstrip().startswith("#")
                ]

        playlists = await asyncio.to_thread(_read_playlists)
        dead: list[dict] = []
        for number, playlist in enumerate(dict.fromkeys(playlists), start=1):
            result = await self._extract_with_retry(browser, playlist)
            if result is None:
                dead.append(
                    {
                        "index": number,
                        "url": playlist,
                        "title": self._short_label(playlist),
                        "folder": "playlists",
                        "status": "ERROR",
                        "reason": "extraction failed after retries",
                    }
                )
                self.console.item_status(
                    number,
                    dead[-1]["title"],
                    "ERROR",
                    dead[-1]["reason"][:120],
                )
            else:
                title, links = result
                folder = self.safe_filename(title) or f"user_playlist_{number:02d}"
                links = [self.normalize_url(link) for link in links]
                self.console.begin_playlist(title or folder, len(links))
                self.console.status(
                    f'Playlist "{title or folder}" -> folder "{folder}" ({len(links)} videos)'
                )
                await self._run_batch(
                    browser,
                    folder,
                    links,
                    outcomes,
                    video_quality,
                    audio_quality,
                    output_format,
                )
            # Проблемные ссылки фиксируются в файле после каждого плейлиста
            self._save_failed(list(outcomes.values()) + dead)
        return dead

    # Извлечение плейлиста с автоматическими повторами; None при неудаче
    async def _extract_with_retry(self, browser, playlist: str) -> tuple[str, list[str]] | None:
        attempts = self.config.download.auto_retries
        for attempt in range(1, attempts + 1):
            try:
                return await self.browser_helper.extract_playlist(browser, playlist)
            except Exception as exc:
                if not is_retryable(exc):
                    self.console.status(f"Playlist failed (non-retryable): {exc}", ok=False)
                    return None
                self.console.status(f"Playlist attempt {attempt}/{attempts} failed", ok=False)
                self.console.write(f"  Reason: {exc}")
                if attempt < attempts:
                    await asyncio.sleep(self.config.download.auto_retry_delay * attempt)
        return None

    # Реальная проверка: запуск утилиты с --version/-version и чтение первой строки
    @staticmethod
    def _probe_version(command: list[str]) -> str | None:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=10, check=False
            )
            lines = (result.stdout or result.stderr or "").strip().splitlines()
            return lines[0][:60] if lines else None
        except Exception:
            return None

    # Предварительная проверка окружения перед стартом
    def preflight(self) -> None:
        profile_path = self.config.paths.browser_profile
        profile_ok = profile_path.exists()
        if self.config.paths.browser_profile_explicit and not profile_ok:
            # Пользователь сам указал путь — его отсутствие это ошибка конфигурации
            raise FirefoxProfileNotFoundError(f"Firefox profile not found: {profile_path}")
        try:
            gecko_path = self.browser_helper._find_geckodriver()
        except Exception:
            gecko_path = None
        gecko_version = self._probe_version([gecko_path, "--version"]) if gecko_path else None
        gecko_ok = bool(gecko_path and gecko_version)
        ffmpeg = self.merger.locate()
        ffmpeg_version = self._probe_version([ffmpeg, "-version"]) if ffmpeg else None
        ffmpeg_ok = bool(ffmpeg and ffmpeg_version)
        free_gb = self._free_ram_gb()
        free_disk = self._free_disk_gb(self.config.paths.output_dir)
        if self.console.is_normal():
            self.console.normal_header("ENVIROMENT")
            self.console.status_line("Firefox profile", profile_ok)
            self.console.status_line("geckodriver", gecko_ok)
            self.console.status_line("ffmpeg", ffmpeg_ok)
            return
        self.console.write("")
        self.console.write("Checking environment...")
        if profile_ok:
            self.console.status(f"Firefox profile found: {profile_path}")
        else:
            self.console.status(
                f"Firefox profile not found: {profile_path} "
                "(current session will be used; set VK_DOWNLOADER_FIREFOX_PROFILE)",
                ok=False,
            )
        if gecko_ok:
            self.console.status(f"geckodriver found - {gecko_path}")
        else:
            self.console.status("geckodriver not found or not runnable", ok=False)
        if ffmpeg_ok:
            self.console.status(f"ffmpeg found: {ffmpeg}")
        else:
            self.console.status("ffmpeg not found or not runnable", ok=False)
        # При малой памяти Firefox выгружает вкладки: "browsing context discarded"
        if 0 < free_gb < 4:
            self.console.status(
                f"Low free memory: {free_gb:.1f} GB — Firefox may discard tabs",
                ok=False,
            )
        if 0 < free_disk < 1:
            self.console.status(
                f"Low free disk: {free_disk:.1f} GB on {self.config.paths.output_dir}",
                ok=False,
            )

    # Свободная ОЗУ в ГБ (0 если не удалось определить) — кроссплатформенно
    @staticmethod
    def _free_ram_gb() -> float:
        # Windows
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return status.ullAvailPhys / 1024**3
        except Exception:
            pass
        # Linux: /proc/meminfo
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return kb / 1024**2
                    if line.startswith("MemFree:"):
                        kb = int(line.split()[1])
                        return kb / 1024**2
        except Exception:
            pass
        # macOS / generic: psutil if available
        try:
            import psutil  # type: ignore[import-not-found]

            return psutil.virtual_memory().available / 1024**3
        except Exception:
            pass
        return 0.0

    # Свободное место на диске в ГБ для указанного пути
    @staticmethod
    def _free_disk_gb(path: Path | str = ".") -> float:
        try:
            free = shutil.disk_usage(str(path)).free
            return free / 1024**3
        except Exception:
            pass
        return 0.0

    # Основной запуск: батчи, автоповторы и ручной ретрай по итогу
    async def run(self) -> int:
        try:
            (
                urls,
                video_q,
                audio_q,
                fmt,
                playlist_url,
                playlist_file,
                _debug_flag,
                advanced,
                debug_mode,
            ) = await asyncio.to_thread(self.parse_args)
            if debug_mode:
                self.config.apply_debug()
            if advanced or debug_mode:
                self.config.ui.mode = "advanced"
                self.console.mode = "advanced"
            else:
                self.config.ui.mode = "normal"
                self.console.mode = "normal"
            if debug_mode:
                self.console.status("Debug mode enabled (logs, mpd dumps, longer timeouts)")
            await asyncio.to_thread(self.config.prepare)
            self.console.banner()
            await asyncio.to_thread(self.preflight)
            browser = await self.browser_helper.launch()
            if self.console.is_normal():
                self.console.status_line("FireFox Connection", True)
            outcomes: dict[tuple, dict] = {}
            dead_entries: list[dict] = []
            try:
                if playlist_file.strip():
                    # Каждый плейлист: сбор ссылок -> скачивание -> следующий плейлист
                    dead_entries = await self._run_playlist_file(
                        browser, playlist_file, outcomes, video_q, audio_q, fmt
                    )
                else:
                    batches = await self._build_batches(browser, urls, playlist_url)
                    for folder, playlist_title, links in batches:
                        if playlist_title:
                            self.console.begin_playlist(playlist_title, len(links))
                        await self._run_batch(
                            browser, folder, links, outcomes, video_q, audio_q, fmt
                        )
                        self._save_failed(list(outcomes.values()))
                # Этап 2: повтор проблемных ссылок после полного прохода
                await self._retry_failed(browser, outcomes, video_q, audio_q, fmt, dead_entries)
                # Недоступные плейлисты попадают в сводку как ERROR
                for position, entry in enumerate(dead_entries, start=1):
                    outcomes[("playlists", position)] = entry
                self._save_failed(list(outcomes.values()))
            finally:
                self.browser_helper.close_player_tab(browser)
                browser.close()
                self.browser_helper.close_driver()
                self._remove_temp()
            self.console.summary(list(outcomes.values()))
            all_ok = bool(outcomes) and all(item["status"] == "DONE" for item in outcomes.values())
            self.console.output_folder(self.config.paths.output_dir)
            self.console.final_box(all_ok)
            return 0
        except Exception as exc:
            # В debug-режиме полный traceback: без него причины сбоев не найти
            if self.config.debug:
                import traceback

                traceback.print_exc()
            self.console.error(str(exc))
            return 1
        finally:
            self.console.close()

    # Полное удаление временной папки после завершения работы
    def _remove_temp(self) -> None:
        temp_dir = self.config.paths.output_dir / self.config.paths.temp_name
        if temp_dir.exists():
            # Снятие скрытого атрибута перед удалением
            try:
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(temp_dir.resolve()), 0x80)
            except Exception:
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)


# === Пример ===
# from vk_downloader.settings import Config
# from vk_downloader.core.downloader import VKMediaDownloader
# downloader = VKMediaDownloader(Config())
# asyncio.run(downloader.run())


if __name__ == "__main__":
    sys.exit(asyncio.run(VKMediaDownloader(Config()).run()))
