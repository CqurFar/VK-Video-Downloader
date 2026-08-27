import asyncio
import contextlib
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from vk_downloader.browser.webdriver_client import FirefoxRemoteSession
from vk_downloader.core.errors import (
    GeckodriverNotFoundError,
    MarionetteDisabledError,
    MPDNotFoundError,
    PlayerNotFoundError,
    PlaylistExtractionError,
    WebDriverError,
)
from vk_downloader.core.session import MediaSession
from vk_downloader.core.urlutils import redact_url
from vk_downloader.settings import Config
from vk_downloader.ui.console import Console


class VKMediaBrowser:
    """ПОЛУЧЕНИЕ MPD И DASH-ТРАФИКА ЧЕРЕЗ ЗАПУЩЕННЫЙ FIREFOX"""

    # Хосты CDNVK: классический vkvdNN.okcdn.ru и новые зеркала *.vkuser.net
    # (например vk6-15.vkuser.net). Превью-хосты (iv./api.okcdn) не подходят.
    CDN_PATTERN = re.compile(
        r"^(?:vkvd\d+\.okcdn\.ru|(?:[\w-]+\.)?vkuser\.net)$",
        re.I,
    )
    MPD_URL_PATTERN = re.compile(r"(?:\.mpd(?:$|[?#])|/manifest(?:[/?#]|$)|/mpd(?:[/?#]|$))", re.I)
    VIDEO_SEGMENT_PATTERN = re.compile(r"(?:/fn/)?track\.v\.m4s(?:$|[?#])", re.I)
    AUDIO_SEGMENT_PATTERN = re.compile(r"(?:/fn/)?track\.a\.m4s(?:$|[?#])", re.I)
    VIDEO_MEDIA_PATTERN = re.compile(r"/fn/s\d+\.v\.m4s(?:$|[?#])", re.I)
    AUDIO_MEDIA_PATTERN = re.compile(r"/fn/s\d+\.a\.m4s(?:$|[?#])", re.I)

    # Однократная активация плеера: play() медиа и кнопки Play во всём DOM включая Shadow DOM
    PLAYER_JS = """
    return (() => {
        const result = {players: 0, videos: 0, audios: 0, clicked: 0, playing: 0};
        const roots = [document];
        const seen = new Set();
        const buttons = [];
        while (roots.length) {
            const root = roots.pop();
            if (!root || seen.has(root)) continue;
            seen.add(root);
            let elements = [];
            try { elements = [...root.querySelectorAll('*')]; } catch (e) {}
            for (const el of elements) {
                try { if (el.shadowRoot) roots.push(el.shadowRoot); } catch (e) {}
            }
            for (const el of elements) {
                try {
                    const tag = el.tagName ? el.tagName.toLowerCase() : '';
                    if (tag === 'vk-video-player') result.players++;
                    if (tag !== 'video' && tag !== 'audio') continue;
                    if (tag === 'video') result.videos++; else result.audios++;
                    if (!el.paused) result.playing++;
                    if (tag === 'video') { el.muted = true; el.volume = 0; el.autoplay = true; el.playsInline = true; }
                    const p = el.play();
                    if (p && typeof p.then === 'function') p.then(() => {}).catch(() => {});
                } catch (e) {}
            }
            try {
                for (const el of root.querySelectorAll('button,[role="button"],[aria-label],[title],[data-testid]')) {
                    if (!buttons.includes(el)) buttons.push(el);
                }
            } catch (e) {}
        }
        const words = ['смотреть', 'play', 'воспроиз', 'начать', 'запуск', 'проиг'];
        const scored = [];
        for (const btn of buttons) {
            const text = `${btn.innerText || ''} ${btn.getAttribute('aria-label') || ''} ${btn.getAttribute('title') || ''} ${btn.getAttribute('data-testid') || ''}`.toLowerCase();
            let score = 0;
            for (const w of words) if (text.includes(w)) { score++; break; }
            if ((btn.innerText || '').trim().toLowerCase().startsWith('смотреть')) score += 10;
            if ((btn.getAttribute('data-testid') || '').includes('unstarted-thumb')) score += 10;
            if (score > 0) scored.push([score, btn]);
        }
        scored.sort((a, b) => b[0] - a[0]);
        const target = scored.length ? scored[0][1] : null;
        if (target) {
            try {
                target.focus();
                target.click();
                result.clicked++;
                const rect = target.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                const opts = {bubbles: true, cancelable: true, composed: true, pointerType: 'mouse', button: 0};
                target.dispatchEvent(new PointerEvent('pointerdown', {...opts, buttons: 1, clientX: x, clientY: y}));
                target.dispatchEvent(new PointerEvent('pointerup', {...opts, buttons: 0, clientX: x, clientY: y}));
            } catch (e) {}
        }
        return result;
    })();
    """

    # Список ресурсов из performance API
    RESOURCES_JS = """
    return performance.getEntriesByType('resource').map(e => ({url: e.name}));
    """

    # Сбор URL из HTML и медиа-атрибутов
    DOM_URLS_JS = """
    const html = document.documentElement ? document.documentElement.outerHTML : '';
    const urls = [...(html.match(/https?:\\/\\/[^\\s"'<>]+/g) || [])];
    for (const el of document.querySelectorAll('video, audio, source, iframe, a, vk-video-player')) {
        for (const attr of ['src', 'href']) {
            const value = el.getAttribute(attr);
            if (value && /^https?:/i.test(value)) urls.push(value);
        }
    }
    return urls;
    """

    # Загрузка текста кандидата прямо в браузере с cookies страницы
    FETCH_JS = """
    const url = arguments[0];
    const done = arguments[arguments.length - 1];
    fetch(url, {credentials: 'include', cache: 'no-store'})
        .then(async response => done({
            status: response.status,
            contentType: response.headers.get('content-type') || '',
            text: await response.text()
        }))
        .catch(error => done({error: String(error)}));
    """

    # Сбор embed-ссылок со страницы плейлиста с автопрокруткой до стабилизации
    PLAYLIST_JS = """
    (() => {
        const done = arguments[arguments.length - 1];
        const found = new Map();
        const add = (oid, id) => {
            if (!/^-?\\d+$/.test(oid) || !/^\\d+$/.test(id)) return;
            const key = oid + '_' + id;
            if (!found.has(key)) {
                found.set(key, 'https://vkvideo.ru/video_ext.php?oid=' +
                    encodeURIComponent(oid) + '&id=' + encodeURIComponent(id));
            }
        };
        const scan = () => {
            let html = '';
            try { html = document.documentElement.outerHTML.replace(/&amp;/g, '&'); } catch (e) {}
            let m;
            const extRe = /video_ext\\.php\\?(?:[^"'<>\\\\\\s]*&)?oid=(-?\\d+)&(?:[^"'<>\\\\\\s]*&)?id=(\\d+)/g;
            while ((m = extRe.exec(html))) { add(m[1], m[2]); }
            const vidRe = /\\/video(-?\\d+)_(\\d+)/g;
            while ((m = vidRe.exec(html))) { add(m[1], m[2]); }
        };
        let stable = 0;
        let last = -1;
        const pick = sel => {
            const texts = [...document.querySelectorAll(sel)].map(el => (el.textContent || '').trim());
            return texts.find(Boolean) || '';
        };
        const tick = () => {
            scan();
            window.scrollTo(0, document.body.scrollHeight);
            if (found.size === last && document.readyState === 'complete') stable++; else stable = 0;
            last = found.size;
            if (stable >= 4) {
                const title = pick('[data-testid="video_playlist_side_block_title"] h1')
                           || pick('[data-testid="breadcrumb-current"] [data-testid="breadcrumb-label"]');
                return done({title, urls: [...found.values()]});
            }
            setTimeout(tick, 500);
        };
        tick();
    })();
    """

    def __init__(self, config: Config, console: Console):
        self.config = config
        self.console = console
        self.geckodriver_process = None
        self.own_geckodriver = False
        self._playback_started = False
        self._player_window: str | None = None

    # Подключение к Firefox: существующая сессия или запуск geckodriver
    async def launch(self) -> FirefoxRemoteSession:
        endpoint = f"http://127.0.0.1:{self.config.browser.geckodriver_port}"
        self.console.write("Connecting to Firefox...")
        if not self.config.paths.browser_profile.exists():
            self.console.status(
                f"Firefox profile not found: {self.config.paths.browser_profile}",
                ok=False,
            )
        session = None
        try:
            session = FirefoxRemoteSession.from_existing(endpoint)
        except Exception:
            session = None
        if session:
            self.console.status("Successful connection")
            return session
        geckodriver = self._find_geckodriver()
        self.geckodriver_process = subprocess.Popen(
            [
                geckodriver,
                "--connect-existing",
                "--marionette-port",
                str(self.config.browser.marionette_port),
                "--port",
                str(self.config.browser.geckodriver_port),
                "--log",
                "error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.own_geckodriver = True
        self.console.spin_start("Waiting for geckodriver")
        deadline = time.monotonic() + max(self.config.browser.connect_timeout, 5)
        while time.monotonic() < deadline:
            try:
                if requests.get(f"{endpoint}/status", timeout=2).ok:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        last_error = None
        for _ in range(10):
            try:
                session = FirefoxRemoteSession.create(endpoint)
                self.console.status("Successful connection")
                return session
            except WebDriverError as exc:
                if "Marionette is disabled" in str(exc):
                    self.console.status(
                        "Marionette is disabled. Start Firefox with the -marionette flag",
                        ok=False,
                    )
                    raise MarionetteDisabledError(
                        "Marionette is disabled in the running Firefox"
                    ) from exc
                # geckodriver держит старую сессию: пробуем подключиться к ней
                if "already started" in str(exc).lower():
                    session = FirefoxRemoteSession.from_existing(endpoint)
                    if session:
                        self.console.status("Successful connection (existing session)")
                        return session
                last_error = exc
                await asyncio.sleep(0.5)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.5)
        # Без этого процесс geckodriver остаётся висеть и держит порт
        self.close_driver()
        raise WebDriverError(f"Could not attach to Firefox: {last_error}")

    # Разворачивание окон Firefox: скрытая вкладка не инициализирует видео и play() висит
    @staticmethod
    def _raise_windows() -> bool:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            handles: list[int] = []

            def callback(hwnd, _lparam):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                if buf.value == "MozillaWindowClass" and user32.IsWindowVisible(hwnd):
                    handles.append(hwnd)
                return True

            user32.EnumWindows(enum_proc(callback), None)
            for hwnd in handles:
                if user32.IsIconic(hwnd):
                    # Показать без активации и без разворота на весь экран
                    user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                # Не вызываем SetForegroundWindow, чтобы не уводить фокус
                # с текущей страницы пользователя
            return bool(handles)
        except Exception:
            return False

    # Вкладка считается скрытой, если окно свёрнуто или не видно
    async def _hidden_tab(self, browser: FirefoxRemoteSession) -> bool:
        try:
            state = browser.execute("return document.visibilityState || '';")
        except Exception:
            return False
        return str(state or "").lower() == "hidden"

    # Поиск geckodriver: PATH -> packages
    def _find_geckodriver(self) -> str:
        candidate = shutil.which("geckodriver")
        if candidate:
            return candidate
        configured = Path(self.config.paths.geckodriver_path)
        if configured.is_file():
            return str(configured.resolve())
        if configured.with_suffix(".exe").is_file():
            return str(configured.with_suffix(".exe").resolve())
        raise GeckodriverNotFoundError(
            "geckodriver was not found in ./packages/geckodriver or PATH"
        )

    # Закрытие geckodriver без закрытия Firefox: terminate -> kill при отказе
    def close_driver(self) -> None:
        if self.geckodriver_process:
            try:
                self.geckodriver_process.terminate()
                self.geckodriver_process.wait(timeout=3)
            except Exception:
                with contextlib.suppress(Exception):
                    self.geckodriver_process.kill()
            self.geckodriver_process = None
            self.own_geckodriver = False

    # Основной сценарий: открыть embed, получить MPD и DASH-трафик
    async def get_mpd(self, browser: FirefoxRemoteSession, url: str) -> MediaSession:
        parent_window = self._recover_context(browser)
        try:
            for attempt in range(1, self.config.browser.context_attempts + 1):
                try:
                    await self._open_player_tab(browser, url)
                    user_agent = browser.execute("return navigator.userAgent;") or "Mozilla/5.0"
                    title = await self._read_title(browser)
                    # Свёрнутое окно => hidden-вкладка: плеер не стартует вовсе
                    if await self._hidden_tab(browser):
                        self._raise_windows()
                        await asyncio.sleep(0.5)
                    mpd_url, mpd_text = await self._collect_mpd(browser, user_agent, url)
                    if not mpd_text:
                        hint = " (playback never started)" if not self._playback_started else ""
                        raise MPDNotFoundError(
                            f"Video player opened, but MPD was not detected{hint}."
                        )
                    # Название часто подменяется JS после гидратации плеера
                    if self._generic_title(title):
                        title = await self._read_title(browser)
                    segment_data = await self._capture_traffic(browser)
                    cookies = browser.get_cookies()
                    return MediaSession(
                        url=url,
                        mpd_url=mpd_url,
                        mpd_text=mpd_text,
                        title=title,
                        user_agent=user_agent,
                        cookies=cookies,
                        video_segment_url=segment_data.get("video_segment_url"),
                        audio_segment_url=segment_data.get("audio_segment_url"),
                        video_segment_base=segment_data.get("video_segment_base"),
                        audio_segment_base=segment_data.get("audio_segment_base"),
                    )
                except MPDNotFoundError:
                    raise
                except Exception as exc:
                    self._player_window = None
                    if attempt >= self.config.browser.context_attempts or not (
                        self._is_transient(exc)
                    ):
                        raise PlayerNotFoundError(
                            f"Player page could not be opened: {exc}"
                        ) from exc
                    self.console.status(
                        f"Browser tab lost, recovering ({attempt}/"
                        f"{self.config.browser.context_attempts})",
                        ok=False,
                    )
                    parent_window = self._recover_context(browser) or parent_window
                    await asyncio.sleep(attempt)
        finally:
            self._back_to_parent(browser, player_window=None, parent_window=parent_window)

    # Переиспользование одной вкладки плеера между видео вместо плодения вкладок
    async def _open_player_tab(self, browser: FirefoxRemoteSession, url: str) -> str:
        reuse = self._player_window
        if reuse:
            try:
                if reuse not in browser.window_handles():
                    self._player_window = None
            except Exception:
                self._player_window = None
        if not self._player_window:
            # Отдельное окно меньше мешает вкладкам пользователя, чем новая вкладка
            new_win = browser.open_new_window()
            if new_win:
                self._player_window = new_win
                with contextlib.suppress(Exception):
                    browser._request(
                        "POST",
                        f"/session/{browser.session_id}/window/rect",
                        {"width": 1280, "height": 800, "x": 10, "y": 10},
                    )
            else:
                self._player_window = browser.open_new_tab()
        browser.switch_window(self._player_window)
        browser.set_script_timeout(self.config.browser.script_timeout)
        browser.execute(
            "if (window.performance) { performance.setResourceTimingBufferSize(20000); performance.clearResourceTimings(); }"
        )
        browser.navigate(url)
        return self._player_window

    # Закрытие общей вкладки плеера при завершении работы приложения
    def close_player_tab(self, browser: FirefoxRemoteSession) -> None:
        window, self._player_window = self._player_window, None
        if not window:
            return
        try:
            if window in browser.window_handles():
                browser.switch_window(window)
                browser.close_window()
        except Exception:
            pass

    # Восстановление выбранного контекста после выгрузки/закрытия вкладок
    def _recover_context(self, browser: FirefoxRemoteSession) -> str | None:
        try:
            return browser.current_window()
        except Exception:
            pass
        try:
            handles = browser.window_handles()
        except Exception:
            return None
        if not handles:
            return None
        target = handles[-1]
        try:
            browser.switch_window(target)
            return target
        except Exception:
            return None

    # Ошибки, при которых имеет смысл пересоздать вкладку и повторить
    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "discarded",
                "no such window",
                "no such browsing context",
                "timed out",
                "timeout",
                "connection reset",
            )
        )

    # Сбор ссылок и названия плейлиста со страницы
    async def extract_playlist(
        self, browser: FirefoxRemoteSession, url: str
    ) -> tuple[str, list[str]]:
        parent_window = browser.current_window() or (browser.window_handles() or [None])[-1]
        try:
            player_window = browser.open_new_tab()
            browser.set_script_timeout(self.config.browser.playlist_script_timeout)
            browser.navigate(url)
        except Exception as exc:
            self._back_to_parent(browser, None, parent_window)
            raise PlayerNotFoundError(f"Playlist page could not be opened: {exc}") from exc
        await asyncio.sleep(self.config.browser.player_start_delay)
        self.console.spin_start("Extracting playlist links")
        title, links = "", []
        try:
            raw = browser.execute_async(self.PLAYLIST_JS)
            payload = raw if isinstance(raw, dict) else {}
            title = str(payload.get("title") or "").strip()
            links = list(
                dict.fromkeys(
                    value for value in (payload.get("urls") or []) if isinstance(value, str)
                )
            )
        except Exception as exc:
            self._back_to_parent(browser, player_window, parent_window)
            raise PlaylistExtractionError(f"{url}: extraction script failed: {exc}") from exc
        if not links:
            self._back_to_parent(browser, player_window, parent_window)
            raise PlaylistExtractionError(
                f"{url}: no video links found (page empty, private or requires login)"
            )
        self.console.status(f"Playlist videos found: {len(links)}")
        self._back_to_parent(browser, player_window, parent_window)
        return title, links

    # Чтение названия видео с повторными попытками
    async def _read_title(self, browser: FirefoxRemoteSession) -> str:
        title_js = (
            "return document.querySelector('meta[property=\"og:title\"]')?.content || "
            "document.title || '';"
        )
        deadline = time.monotonic() + self.config.browser.title_wait_seconds
        while True:
            title = (browser.execute(title_js) or "").strip()
            if not self._generic_title(title) or time.monotonic() > deadline:
                return title
            await asyncio.sleep(0.5)

    # Заглушки вместо названия: пустые и стартовые заголовки до гидратации плеера
    @staticmethod
    def _generic_title(title: str) -> bool:
        value = (title or "").strip().lower()
        if not value:
            return True
        if value in {"vk video", "vkvideo", "video", "vk видео"}:
            return True
        return "смотреть онлайн бесплатно" in value

    # Опрос ресурсов страницы и попытки загрузить MPD-манифест
    async def _collect_mpd(self, browser: FirefoxRemoteSession, user_agent: str, referer: str):
        candidates, attempted = [], set()
        retry_delay = self.config.browser.player_retry_delay
        deadline = time.monotonic() + max(self.config.browser.mpd_collect_timeout, 15)
        player_seen, activated, last_try = False, False, 0.0
        self._playback_started = False
        self.console.spin_start("Waiting for *.mpd manifest")
        while time.monotonic() < deadline:
            # Плеер запускается повторно, пока воспроизведение не началось:
            # ранний клик может уйти в негидрированный компонент
            now = time.monotonic()
            if not activated and now - last_try >= retry_delay:
                last_try = now
                if await self._hidden_tab(browser):
                    self._raise_windows()
                try:
                    stats = browser.execute(self.PLAYER_JS)
                    stats = stats if isinstance(stats, dict) else {}
                except Exception:
                    stats = {}
                if stats.get("players") and not player_seen:
                    player_seen = True
                    self.console.status("VK player detected")
                if stats.get("playing"):
                    activated = True
                    self._playback_started = True
                    self.console.status("Playback started")
            resources = self._resource_entries(browser)
            extra_urls = await self._dom_urls(browser)
            for value in resources + extra_urls:
                if value and value not in candidates:
                    candidates.append(value)
            fresh = [c for c in candidates if c not in attempted]
            attempted.update(fresh)
            result = await self._try_candidates(browser, fresh, user_agent, referer)
            if result:
                mpd_url, mpd_text = result
                self.console.section("MPD")
                self.console.write("*.mpd Status: Accepted")
                self.console.write(f"*.mpd Link: {redact_url(mpd_url)}")
                return mpd_url, mpd_text
            await asyncio.sleep(0.5)
        return None, None

    # Наблюдение за DASH-трафиком до появления обоих initialization-сегментов
    async def _capture_traffic(self, browser: FirefoxRemoteSession) -> dict:
        self.console.spin_start("Starting playback and collecting DASH traffic")
        capture_started = time.monotonic()
        min_capture = self.config.browser.traffic_capture_seconds
        capture_deadline = (
            capture_started + min_capture + max(self.config.browser.traffic_capture_timeout, 10)
        )
        segment_data = {}
        while time.monotonic() < capture_deadline:
            segment_data = self._extract_segment_data(self._resource_entries(browser))
            elapsed = time.monotonic() - capture_started
            both_ready = segment_data.get("video_segment_url") and segment_data.get(
                "audio_segment_url"
            )
            if both_ready and elapsed >= min_capture:
                break
            await asyncio.sleep(0.5)
        # Финальный срез: _resource_entries/_extract_segment_data уже безопасны
        segment_data = self._extract_segment_data(self._resource_entries(browser))
        self.console.write("")
        self.console.write(
            "Detected video segment: " + redact_url(segment_data.get("video_segment_url"))
        )
        self.console.write(
            "Detected audio segment: " + redact_url(segment_data.get("audio_segment_url"))
        )
        self.console.write(
            "Detected video CDN base: " + redact_url(segment_data.get("video_segment_base"))
        )
        self.console.write(
            "Detected audio CDN base: " + redact_url(segment_data.get("audio_segment_base"))
        )
        self.console.status("DASH traffic collected")
        return segment_data

    # Ресурсы performance API текущей вкладки
    def _resource_entries(self, browser: FirefoxRemoteSession) -> list[str]:
        try:
            entries = browser.execute(self.RESOURCES_JS) or []
            return [entry.get("url", "") for entry in entries if isinstance(entry, dict)]
        except Exception:
            return []

    # URL из DOM текущей вкладки
    async def _dom_urls(self, browser: FirefoxRemoteSession) -> list[str]:
        try:
            return list(dict.fromkeys(browser.execute(self.DOM_URLS_JS) or []))
        except Exception:
            return []

    # Перебор кандидатов: сначала похожие на MPD, затем прочие CDN-ссылки
    async def _try_candidates(
        self,
        browser: FirefoxRemoteSession,
        candidates: list[str],
        user_agent: str,
        referer: str,
    ):
        prioritized = sorted(
            candidates,
            key=lambda v: (
                0 if self.MPD_URL_PATTERN.search(v) else 1,
                0 if self.CDN_PATTERN.match(urlparse(v).hostname or "") else 1,
                len(v),
            ),
        )
        for candidate in dict.fromkeys(prioritized):
            if not self._is_candidate(candidate):
                continue
            text = await self._fetch_in_browser(browser, candidate)
            if text:
                return candidate, text
            text = await asyncio.to_thread(
                self._fetch_with_session,
                candidate,
                user_agent,
                referer,
                browser.get_cookies(),
            )
            if text:
                return candidate, text
        return None

    # Похоже ли URL на MPD-манифест VK CDN
    def _is_candidate(self, url: str) -> bool:
        hostname = urlparse(url).hostname or ""
        return bool(self.MPD_URL_PATTERN.search(url) or self.CDN_PATTERN.match(hostname))

    # Загрузка кандидата через fetch внутри браузера
    async def _fetch_in_browser(self, browser: FirefoxRemoteSession, url: str) -> str | None:
        try:
            result = browser.execute_async(self.FETCH_JS, [url])
        except Exception:
            return None
        if not isinstance(result, dict) or result.get("status") not in (200, 206):
            return None
        text = result.get("text", "")
        return text if self._looks_like_mpd(text) else None

    # Загрузка кандидата через requests с cookies браузера
    def _fetch_with_session(
        self, url: str, user_agent: str, referer: str, cookies: list[dict]
    ) -> str | None:
        with requests.Session() as session:
            for cookie in cookies:
                try:
                    if cookie.get("name"):
                        session.cookies.set(
                            cookie["name"],
                            cookie.get("value", ""),
                            domain=cookie.get("domain") or None,
                            path=cookie.get("path") or "/",
                        )
                except Exception:
                    continue
            headers = {
                "User-Agent": user_agent,
                "Referer": referer,
                "Origin": "https://vk.ru" if self._is_vk_host(referer) else "",
                "Accept": "application/dash+xml, application/xml, text/xml, */*",
                # Без br: requests без extras не умеет brotli, CDN вернёт gzip/identity
                "Accept-Encoding": "gzip, deflate",
            }
            try:
                response = session.get(
                    url, headers=headers, timeout=self.config.browser.mpd_timeout
                )
                if response.status_code not in (200, 206):
                    return None
                # content сам распакован; raw.read() отдавал бы сжатые байты
                text = response.content[: 1024 * 1024].decode("utf-8", errors="replace")
                return text if self._looks_like_mpd(text) else None
            except Exception:
                return None

    # Проверка хоста на принадлежность VK
    @staticmethod
    def _is_vk_host(url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host.removeprefix("www.")
        return host in {"vk.com", "vkvideo.ru", "vk.ru"}

    # Похоже ли содержимое на DASH-манифест: только по телу, не по Content-Type
    @staticmethod
    def _looks_like_mpd(text: str) -> bool:
        head = text.lstrip("\ufeff \t\r\n")[:4096]
        return head.startswith("<?xml") or "<MPD" in head

    # Извлечение initialization/media сегментов из списка ресурсов
    def _extract_segment_data(self, resources: list[str]) -> dict:
        patterns = [
            (self.VIDEO_SEGMENT_PATTERN, "video"),
            (self.AUDIO_SEGMENT_PATTERN, "audio"),
            (self.VIDEO_MEDIA_PATTERN, "video"),
            (self.AUDIO_MEDIA_PATTERN, "audio"),
        ]
        found = {}
        for pattern, kind in patterns:
            if kind in found:
                continue
            for resource_url in resources:
                if pattern.search(resource_url):
                    found[kind] = resource_url
                    break
        return {
            "video_segment_url": found.get("video"),
            "audio_segment_url": found.get("audio"),
            "video_segment_base": self._segment_base_from_url(found.get("video")),
            "audio_segment_base": self._segment_base_from_url(found.get("audio")),
        }

    # База CDN для относительных SegmentTemplate из реального URL сегмента
    @staticmethod
    def _segment_base_from_url(segment_url: str | None) -> str | None:
        if not segment_url:
            return None
        parsed = urlparse(segment_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        marker = "/ondemand/"
        index = parsed.path.find(marker)
        prefix = parsed.path[: index + len(marker)] if index >= 0 else parsed.path.rsplit("/", 1)[0]
        return f"{parsed.scheme}://{parsed.netloc}{prefix.rstrip('/')}/"

    # Закрытие вкладки плеера и возврат на исходную
    @staticmethod
    def _back_to_parent(
        browser: FirefoxRemoteSession,
        player_window: str | None,
        parent_window: str | None,
    ) -> None:
        try:
            if player_window and browser.current_window() == player_window:
                browser.close_window()
            if parent_window and parent_window in browser.window_handles():
                browser.switch_window(parent_window)
        except Exception:
            pass


# === Пример ===
# from vk_downloader.settings import Config
# from vk_downloader.browser.vk_media_browser import VKMediaBrowser
# helper = VKMediaBrowser(Config(), Console(Config()))
# browser = asyncio.run(helper.launch())
