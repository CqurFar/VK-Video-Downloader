import asyncio
import contextlib
import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

from vk_downloader.core.errors import SegmentDownloadError
from vk_downloader.download.concurrency_gate import ConcurrencyGate
from vk_downloader.download.worker_balancer import WorkerBalancer
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console
from vk_downloader.ui.fs_paths import FSPaths


class DashDownloader:
    """DASH ТРЕК: INIT + СЕГМЕНТЫ"""

    def __init__(self, config: Config, console: Console):
        self.config = config
        self.console = console
        self._http_local = threading.local()

    # Thread-local HTTP сессия с пулом под максимальное число worker
    def _session(self, headers: dict[str, str]) -> requests.Session:
        session = getattr(self._http_local, "session", None)
        if session is None:
            pool = max(self.config.download.workers_max, 8)
            adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=0)
            session = requests.Session()
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(headers)
            self._http_local.session = session
        return session

    # Заголовки для CDN запросов сегментов
    @staticmethod
    def media_headers(user_agent: str, source_url: str, segment_url: str | None) -> dict[str, str]:
        source = urlparse(source_url)
        segment = urlparse(segment_url or "")
        origin = f"{source.scheme}://{source.netloc}" if source.scheme and source.netloc else ""
        referer = (
            f"{segment.scheme}://{segment.netloc}/" if segment.scheme and segment.netloc else ""
        )
        return {
            "User-Agent": user_agent,
            "Referer": referer,
            "Origin": origin,
            "Accept": "*/*",
            "Connection": "keep-alive",
        }

    # Подстановка $Number$/$Time$ в шаблон и склейка с базой
    @staticmethod
    def resolve_segment_url(
        base: str, value: str, number: int | None = None, time_value: int | None = None
    ) -> str:
        value = (value or "").strip().replace("\\/", "/")
        if not value:
            return ""
        if number is not None:
            value = value.replace("$Number$", str(number))
        if time_value is not None:
            value = value.replace("$Time$", str(time_value))
        return urljoin(base, value)

    # База CDN: приоритет у детектированной из трафика, затем /ondemand/ из MPD
    @staticmethod
    def segment_base(track: dict, detected_base: str | None, mpd_url: str) -> str:
        parsed = urlparse(detected_base or "")
        if detected_base and parsed.scheme and parsed.netloc:
            return detected_base.rstrip("/") + "/"
        parsed = urlparse(track.get("url") or "")
        if parsed.scheme and parsed.netloc:
            marker, index = "/ondemand/", parsed.path.find("/ondemand/")
            if index >= 0:
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path[: index + len(marker)]}"
        parsed = urlparse(mpd_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rsplit('/', 1)[0]}/"
        raise RuntimeError("Could not determine DASH segment base URL")

    # Скачивание одного ресурса с повторными попытками и атомарной заменой
    def _download_resource(
        self,
        url: str,
        headers: dict[str, str],
        output: Path,
        label: str,
        cookies: "requests.cookies.RequestsCookieJar | None" = None,
    ) -> int:
        target = Path(FSPaths.long(output))
        tmp = Path(FSPaths.long(output.with_name(output.name + ".tmp")))
        for attempt in range(1, self.config.download.retries + 1):
            try:
                response = self._session(headers).get(
                    url,
                    timeout=self.config.download.request_timeout,
                    stream=True,
                    cookies=cookies,
                )
                if response.status_code != 200:
                    response.close()
                    raise RuntimeError(f"HTTP {response.status_code}")
                total = 0
                with tmp.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
                            total += len(chunk)
                response.close()
                if self._looks_like_html(tmp):
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("CDN returned an HTML error page instead of media")
                if total <= 0:
                    raise RuntimeError("CDN returned empty response")
                os.replace(tmp, target)
                return total
            except Exception as exc:
                if attempt == self.config.download.retries:
                    raise RuntimeError(f"{label}: {exc}") from exc
                # Экспоненциальная задержка: 5xx у CDN обычно проходят со временем
                time.sleep(min(2**attempt, 8))
        return 0

    # Манифест isoff-on-demand: трек целиком одним файлом по BaseURL из MPD
    async def _download_single_file(
        self,
        track: dict,
        headers: dict[str, str],
        output: Path,
        label: str,
        cookies: "requests.cookies.RequestsCookieJar | None" = None,
    ) -> None:
        url = (track.get("url") or "").strip()
        parsed = urlparse(url)
        if not (parsed.scheme and parsed.netloc):
            raise RuntimeError(f"{label}: no SegmentTemplate and no downloadable BaseURL")
        self.console.write(f"{label} single-file mode")
        total = await asyncio.to_thread(
            self._download_with_progress, url, headers, output, label, cookies
        )
        self.console.write(f"{label} downloaded: {total // (1024 * 1024)} MB")

    # Скачивание файла с прогресс-баром и повторными попытками
    def _download_with_progress(
        self,
        url: str,
        headers: dict[str, str],
        output: Path,
        label: str,
        cookies: "requests.cookies.RequestsCookieJar | None" = None,
    ) -> int:
        target = Path(FSPaths.long(output))
        tmp = Path(FSPaths.long(output.with_name(output.name + ".tmp")))
        started = time.time()
        for attempt in range(1, self.config.download.retries + 1):
            done = 0
            response = None
            tmp.unlink(missing_ok=True)
            try:
                response = self._session(headers).get(
                    url,
                    timeout=self.config.download.request_timeout,
                    stream=True,
                    cookies=cookies,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                expected = int(response.headers.get("Content-Length") or 0)
                last_paint = 0.0
                with tmp.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if expected and now - last_paint >= 0.3:
                                last_paint = now
                                self.console.progress(label, done, expected, started)
            except Exception as exc:
                if attempt == self.config.download.retries:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"{label}: {exc}") from exc
                # Экспоненциальная задержка перед повтором обрыва
                time.sleep(min(2**attempt, 8))
                continue
            finally:
                if response is not None:
                    response.close()
            if done <= 0:
                tmp.unlink(missing_ok=True)
                if attempt == self.config.download.retries:
                    raise RuntimeError(f"{label}: CDN returned empty response")
                time.sleep(min(2**attempt, 8))
                continue
            if self._looks_like_html(tmp):
                tmp.unlink(missing_ok=True)
                if attempt == self.config.download.retries:
                    raise RuntimeError(f"{label}: CDN returned an HTML error page")
                time.sleep(min(2**attempt, 8))
                continue
            if expected and done < expected:
                tmp.unlink(missing_ok=True)
                if attempt == self.config.download.retries:
                    raise RuntimeError(f"{label}: incomplete download {done}/{expected} bytes")
                time.sleep(min(2**attempt, 8))
                continue
            try:
                with tmp.open("rb") as f, contextlib.suppress(OSError):
                    os.fsync(f.fileno())
                os.replace(tmp, target)
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"{label}: atomic replace failed: {exc}") from exc
            if expected:
                self.console.progress(label, expected, expected, started)
            else:
                self.console.write(f"{label}: {done} bytes")
            return done
        return 0

    # Путь части сегмента по индексу
    @staticmethod
    def _part_path(parts_dir: Path, index: int) -> Path:
        return parts_dir / f"{index + 1:08d}.part"

    # Часть считается готовой только если файл существует и непустой
    @staticmethod
    def _valid_part(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    # Кусок похож на HTML-страницу ошибки, а не на медиаданные
    @staticmethod
    def _looks_like_html(path: Path) -> bool:
        try:
            with path.open("rb") as file:
                head = file.read(512)
        except OSError:
            return False
        text = head.lstrip().lower()
        return text.startswith(b"<!doctype") or (text[:1] == b"<" and b"<html" in text)

    # Полное скачивание трека: init, затем сегменты через постоянный пул.
    # Готовые части предыдущих попыток переиспользуются (резюм на 99%+)
    async def download_track(
        self,
        track: dict,
        detected_base: str | None,
        mpd_url: str,
        headers: dict[str, str],
        output: Path,
        label: str,
        temp_dir: Path,
        cookies: "requests.cookies.RequestsCookieJar | None" = None,
    ) -> None:
        template = track.get("segment_template") or {}
        segments = track.get("segment_timeline") or []
        media = template.get("media")
        if not media or not segments:
            await self._download_single_file(track, headers, output, label, cookies)
            return
        parts_dir = temp_dir / f".{output.stem[:40]}.parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        base = self.segment_base(track, detected_base, mpd_url)
        init_path = parts_dir / "00000000.init"
        if not self._valid_part(init_path):
            init_url = self.resolve_segment_url(base, template.get("initialization", ""))
            if not init_url:
                raise RuntimeError(f"{label}: invalid initialization URL")
            self._download_resource(
                init_url, headers, init_path, f"{label} initialization", cookies
            )
        pending = [
            index
            for index in range(len(segments))
            if not self._valid_part(self._part_path(parts_dir, index))
        ]
        resumed = len(segments) - len(pending)
        if resumed:
            self.console.write(f"{label} resumed from cache: {resumed}/{len(segments)} segments")
        else:
            self.console.write(f"{label} segments: {len(segments)}")

        executor = ThreadPoolExecutor(
            max_workers=self.config.download.workers_max,
            thread_name_prefix="vk-dash",
        )
        try:
            await self._download_segments(
                executor, segments, pending, base, media, headers, parts_dir, label, cookies
            )
        finally:
            executor.shutdown(wait=True)
        self.assemble(parts_dir, len(segments), output)
        # Части удаляются только после успешной склейки: иначе резюм следующей попытки
        shutil.rmtree(parts_dir, ignore_errors=True)

    # Параллельная загрузка сегментов: скользящее окно + спасательная фаза для отказов
    async def _download_segments(
        self,
        executor: ThreadPoolExecutor,
        segments: list[dict],
        pending: list[int],
        base: str,
        media: str,
        headers: dict[str, str],
        parts_dir: Path,
        label: str,
        cookies: "requests.cookies.RequestsCookieJar | None" = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        download_cfg = self.config.download
        balancer = WorkerBalancer(
            download_cfg.initial_workers(len(pending)),
            maximum=download_cfg.workers_max,
            minimum=download_cfg.workers_min,
            grow_after=self.config.balance.grow_after,
            step=self.config.balance.step,
        )
        gate = ConcurrencyGate(balancer.value)
        # Кэшированные с прошлой попытки сразу считаются завершёнными
        finished = set(range(len(segments))) - set(pending)
        running = {}
        tail_freeze = len(segments) >= download_cfg.tail_min_segments
        started = time.time()

        # Задача сегмента: ожидание слота в гейте и скачивание в пуле
        async def one(index: int):
            segment = segments[index]
            part_file = self._part_path(parts_dir, index)

            def job():
                if self._valid_part(part_file):
                    return 0
                url = self.resolve_segment_url(
                    base, media, number=segment["number"], time_value=segment["time"]
                )
                with gate:
                    return self._download_resource(
                        url, headers, part_file, f"{label} segment {index + 1}", cookies
                    )

            try:
                await loop.run_in_executor(executor, job)
                return index, None
            except Exception as exc:
                return index, exc

        # Дозаполнение окна активных задач под текущий лимит
        def pump(queue: deque) -> None:
            while queue and len(running) < balancer.value:
                task = asyncio.ensure_future(one(queue.popleft()))
                running[task] = True

        # Прокачка очереди до исчерпания; отказы не обрывают остальные сегменты
        async def drain(queue: deque, max_attempts: int) -> list[tuple[int, Exception]]:
            attempts, dead = {}, []
            while True:
                pump(queue)
                if not running:
                    return dead
                done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
                retry = []
                for task in done:
                    running.pop(task)
                    index, exc = task.result()
                    if exc is None:
                        finished.add(index)
                        balancer.success()
                        continue
                    balancer.failure()
                    attempts[index] = attempts.get(index, 0) + 1
                    if attempts[index] >= max_attempts:
                        dead.append((index, exc))
                    else:
                        retry.append(index)
                # На финише фиксируем workers: колебания у 99% только вредят
                if (
                    tail_freeze
                    and not balancer.frozen
                    and len(finished) >= len(segments) * download_cfg.tail_ratio
                ):
                    balancer.freeze()
                gate.set(balancer.value)
                queue.extend(retry)
                shown = min(len(finished), len(segments))
                self.console.progress(label, shown, len(segments), started, workers=balancer.value)

        dead = await drain(deque(pending), download_cfg.max_passes)

        # Спасательная фаза: пауза даёт CDN время уйти от 5xx, workers снижены заморозкой
        if dead:
            await asyncio.sleep(download_cfg.rescue_delay)
            balancer.freeze()
            gate.set(balancer.value)
            self.console.write("")
            self.console.write(f"{label} retrying {len(dead)} failed segments")
            dead = await drain(deque(index for index, _ in dead), download_cfg.rescue_passes)

        if dead:
            numbers = ", ".join(str(index + 1) for index, _ in dead[:10])
            raise SegmentDownloadError(
                f"{label}: {len(dead)} segment(s) failed permanently: [{numbers}]"
            )

    # Склейка init и сегментов в итоговый файл (атомарно)
    @staticmethod
    def assemble(parts_dir: Path, segment_count: int, output: Path) -> None:
        tmp = output.with_name(output.name + ".tmp")
        with Path(FSPaths.long(tmp)).open("wb") as final:
            with (parts_dir / "00000000.init").open("rb") as init_file:
                shutil.copyfileobj(init_file, final, length=1024 * 1024)
            for index in range(segment_count):
                with (parts_dir / f"{index + 1:08d}.part").open("rb") as part:
                    shutil.copyfileobj(part, final, length=1024 * 1024)
        # fsync + atomic replace
        try:
            with Path(FSPaths.long(tmp)).open("rb") as f, contextlib.suppress(OSError):
                os.fsync(f.fileno())
            os.replace(tmp, output)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise


# === Пример ===
# from vk_downloader.settings import Config
# from vk_downloader.media.dash_downloader import DashDownloader
# from vk_downloader.ui.console import Console
# downloader = DashDownloader(Config(), Console(Config()))
# asyncio.run(downloader.download_track(track, base, mpd_url, headers,
#                                          out_path, "Video Track", temp_dir))
