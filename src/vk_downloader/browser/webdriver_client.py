import contextlib
import logging
import time

import requests

from vk_downloader.core.errors import WebDriverError
from vk_downloader.core.urlutils import is_vk_host

logger = logging.getLogger(__name__)


class FirefoxRemoteSession:
    """WEBDRIVER-КЛИЕНТ ДЛЯ ПОДКЛЮЧЕНИЯ К СЕССИИ FIREFOX"""

    def __init__(self, endpoint: str, session_id: str):
        self.endpoint = endpoint.rstrip("/")
        self.session_id = session_id

    # Базовый запрос к WebDriver API
    def _request(self, method: str, path: str, data=None):
        response = requests.request(
            # Перегруженный Firefox отвечает медленно: короткий таймаут даёт ложные обрывы
            method,
            f"{self.endpoint}{path}",
            json=data,
            timeout=120,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WebDriverError(f"WebDriver returned HTTP {response.status_code}") from exc
        value = payload.get("value")
        if response.status_code >= 400 or (isinstance(value, dict) and value.get("error")):
            value = value if isinstance(value, dict) else {}
            message = value.get("message") or str(value)
            # geckodriver не смог достучаться до Marionette в запущенном Firefox
            if "marionette" in message.lower() or "connection refused" in message.lower():
                raise WebDriverError("Marionette is disabled in the running Firefox")
            raise WebDriverError(message)
        return value

    # Создание новой WebDriver-сессии
    @classmethod
    def create(cls, endpoint: str) -> "FirefoxRemoteSession":
        response = requests.post(
            f"{endpoint.rstrip('/')}/session",
            json={"capabilities": {"alwaysMatch": {"browserName": "firefox"}}},
            timeout=30,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WebDriverError(
                f"Invalid WebDriver response: HTTP {response.status_code}"
            ) from exc
        value = payload.get("value", {})
        if response.status_code >= 400 or (isinstance(value, dict) and value.get("error")):
            message = value.get("message") if isinstance(value, dict) else str(value)
            message = message or str(value)
            if "marionette" in message.lower() or "connection refused" in message.lower():
                raise WebDriverError("Marionette is disabled in the running Firefox")
            raise WebDriverError(message)
        session_id = payload.get("sessionId") or value.get("sessionId")
        if not session_id:
            raise WebDriverError("WebDriver did not return sessionId")
        return cls(endpoint, session_id)

    # Подключение к уже существующей сессии geckodriver
    @classmethod
    def from_existing(
        cls, endpoint: str, preferred_id: str | None = None
    ) -> "FirefoxRemoteSession | None":
        try:
            response = requests.get(f"{endpoint.rstrip('/')}/sessions", timeout=5)
        except requests.RequestException:
            return None
        if response.status_code >= 400:
            return None
        try:
            sessions = response.json().get("value", [])
        except ValueError:
            return None
        if not sessions:
            return None
        chosen = cls._select_session(endpoint, sessions, preferred_id)
        return cls(endpoint, chosen) if chosen else None

    # Выбор подходящей сессии: явный id > firefox > VK-вкладка > первая
    @classmethod
    def _select_session(
        cls, endpoint: str, sessions: list[dict], preferred_id: str | None
    ) -> str | None:
        def sid_of(session: dict) -> str | None:
            return session.get("id") or session.get("sessionId")

        def is_firefox(session: dict) -> bool:
            caps = session.get("capabilities", {}) or {}
            name = caps.get("browserName") or caps.get("alwaysMatch", {}).get("browserName")
            return name == "firefox"

        if preferred_id:
            for session in sessions:
                if sid_of(session) == preferred_id:
                    return preferred_id
        firefox = [s for s in sessions if is_firefox(s)]
        candidates = firefox or sessions
        if len(candidates) == 1:
            return sid_of(candidates[0])
        for session in candidates:
            sid = sid_of(session)
            if sid and cls._is_vk_url(endpoint, sid):
                return sid
        if len(candidates) > 1:
            logger.warning(
                "Multiple WebDriver sessions (%d); using first: %s",
                len(candidates),
                sid_of(candidates[0]),
            )
        return sid_of(candidates[0])

    # Текущий URL вкладки сессии (None при ошибке)
    @staticmethod
    def _is_vk_url(endpoint: str, session_id: str) -> bool:
        try:
            response = requests.get(f"{endpoint.rstrip('/')}/session/{session_id}/url", timeout=3)
        except requests.RequestException:
            return False
        if response.status_code >= 400:
            return False
        try:
            url = response.json().get("value", "")
        except ValueError:
            return False
        return bool(url) and is_vk_host(url)

    # Переход по URL
    def navigate(self, url: str) -> None:
        self._request("POST", f"/session/{self.session_id}/url", {"url": url})

    # Список открытых вкладок
    def window_handles(self) -> list[str]:
        return self._request("GET", f"/session/{self.session_id}/window/handles") or []

    # Текущая вкладка
    def current_window(self) -> str | None:
        return self._request("GET", f"/session/{self.session_id}/window")

    # Переключение на вкладку
    def switch_window(self, handle: str) -> None:
        self._request("POST", f"/session/{self.session_id}/window", {"handle": handle})

    # Закрытие текущей вкладки
    def close_window(self) -> list[str]:
        return self._request("DELETE", f"/session/{self.session_id}/window") or []

    # Открытие новой вкладки (в фоне, не уводя фокус с текущей)
    def open_new_tab(self) -> str:
        parent = None
        with contextlib.suppress(Exception):
            parent = self.current_window()
        handles_before = set(self.window_handles())
        self.execute("window.open('about:blank', '_blank');")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            new_handles = [h for h in self.window_handles() if h not in handles_before]
            if new_handles:
                new_handle = new_handles[-1]
                # Держим фокус на родительской вкладке, чтобы не перекидывать пользователя
                if parent and parent in self.window_handles():
                    try:
                        self.switch_window(parent)
                    except Exception:
                        self.switch_window(new_handle)
                        return new_handle
                else:
                    self.switch_window(new_handle)
                return new_handle
            time.sleep(0.1)
        raise WebDriverError("Firefox did not create a new tab")

    # Попытка открыть отдельное окно для плеера (меньше мешает вкладкам)
    def open_new_window(self) -> str | None:
        try:
            value = self._request(
                "POST", f"/session/{self.session_id}/window/new", {"type": "window"}
            )
            handle = value.get("handle") if isinstance(value, dict) else None
            if handle:
                # Сразу возвращаем фокус родителю, если он есть
                try:
                    # value может содержать предыдущий handle, но переключимся назад
                    handles = self.window_handles()
                    # новый handle уже текущий, найдём старый
                    old = [h for h in handles if h != handle]
                    if old:
                        self.switch_window(old[-1])
                except Exception:
                    pass
                return handle
        except Exception:
            pass
        return None

    # Синхронный JS в контексте страницы
    def execute(self, script: str, args: list | None = None):
        return self._request(
            "POST",
            f"/session/{self.session_id}/execute/sync",
            {"script": script, "args": args or []},
        )

    # Асинхронный JS в контексте страницы
    def execute_async(self, script: str, args: list | None = None):
        return self._request(
            "POST",
            f"/session/{self.session_id}/execute/async",
            {"script": script, "args": args or []},
        )

    # Таймаут выполнения JS
    def set_script_timeout(self, seconds: int) -> None:
        self._request("POST", f"/session/{self.session_id}/timeouts", {"script": seconds * 1000})

    # Cookies текущей сессии
    def get_cookies(self) -> list[dict]:
        return self._request("GET", f"/session/{self.session_id}/cookie") or []

    # Закрытие клиента без завершения сессии Firefox
    def close(self) -> None:
        pass


# === Пример ===
# from vk_downloader.browser.webdriver_client import FirefoxRemoteSession
# browser = FirefoxRemoteSession.from_existing("http://127.0.0.1:4444")
# browser.navigate("https://vkvideo.ru")
