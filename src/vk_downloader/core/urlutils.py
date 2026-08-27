"""Утилиты имён файлов, URL и куков — чистые функции, вынесены из God Object."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import urlparse


def safe_filename(name: str, config) -> str:
    """Безопасное имя файла из заголовка с обрезкой под лимит Windows."""
    naming = config.naming
    name = re.sub(r"\s+", " ", naming.safe_name_re.sub("_", name)).strip()
    return name[: naming.max_name].rstrip(". ") or naming.fallback_name


def short_label(url: str) -> str:
    """Короткая метка ссылки до разбора заголовка."""
    match = re.search(r"[?&]id=(\d+)", url)
    return f"VK-{match.group(1)}" if match else url[-45:]


_SIGNED_PARAM_RE = re.compile(
    r"((?:[?&]|&amp;)(?:token|sig2?|hash|extra|expires?|exp|hdnts|src|hd|uid)[^=&]*)=[^&\"'<>\\s]+",
    re.I,
)

_SIGNED_KEYS = ("token", "sig", "hash", "extra", "expires", "exp", "hdnts", "src", "hd", "uid")

# Доверенные хосты VK: входные ссылки и MPD допускаются только с них
VK_HOSTS = frozenset({"vk.com", "vkvideo.ru", "vk.ru"})
# CDN VK: классический vkvdNN.okcdn.ru и зеркала *.vkuser.net
VK_CDN_PATTERN = re.compile(
    r"^(?:vkvd\d+\.okcdn\.ru|(?:[\w-]+\.)?vkuser\.net)$",
    re.I,
)


def is_vk_host(url: str) -> bool:
    """Ссылка ведёт на один из официальных доменов VK по https."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host in VK_HOSTS


def is_vk_cdn(url: str) -> bool:
    """URL указывает на CDN VK (okcdn/vkuser)."""
    if not url:
        return False
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    return bool(VK_CDN_PATTERN.match(host))


def is_vk_trusted(url: str) -> bool:
    """Любой доверенный хост VK: сайт либо CDN."""
    return is_vk_host(url) or is_vk_cdn(url)


def validate_input_url(url: str) -> str:
    """Нормализация ссылки и жёсткая проверка, что это VK.

    Проверяем хост исходной ссылки ДО нормализации: ``normalize_url`` сам
    подставляет хост ``vkvideo.ru``, поэтому валидация после него всегда проходила
    бы. Посторонние хосты отбрасываются до любого обращения к браузеру/сети.
    """
    if not is_vk_host(url):
        raise ValueError(f"Not a VK video URL: {url}")
    return normalize_url(url)


def redact_mpd(text: str) -> str:
    """Маскирование signed URL в debug-дампах mpd (token/sig/hashes)."""
    return _SIGNED_PARAM_RE.sub(r"\1=***", text)


def redact_url(url: str | None) -> str:
    """Безопасный URL для логов: маскирует query-параметры с секретами."""
    if not url:
        return "-"
    return _SIGNED_PARAM_RE.sub(r"\1=***", url)


def redact_text(text: str) -> str:
    """Маскирует любые signed URL внутри свободного текста/HTML."""
    if not text:
        return text
    return _SIGNED_PARAM_RE.sub(r"\1=***", text)


def is_signed_url(url: str) -> bool:
    """Есть ли в URL подписанные параметры."""
    return bool(_SIGNED_PARAM_RE.search(url or ""))


def _path_matches(cookie_path: str, request_path: str) -> bool:
    """RFC6265 §5.1.4: cookie path — префикс пути запроса (до следующего /)."""
    if not cookie_path or cookie_path == "/":
        return True
    if not request_path.startswith(cookie_path):
        return False
    return len(request_path) == len(cookie_path) or request_path[len(cookie_path)] == "/"


def filter_cookies(
    cookies: list[dict],
    *urls: str | None,
) -> list[dict]:
    """Фильтрация cookies по domain/path — не шлём лишние session cookies на CDN.

    Cookie попадает в выдачу, только если его domain совпадает с одним из
    хостов запроса И его path является префиксом пути хотя бы одного запроса.
    Это закрывает утечку path-scoped cookies на чужие пути/хосты.
    """
    if not cookies:
        return []
    allowed_hosts: set[str] = set()
    allowed_paths: list[str] = []
    for u in urls:
        if not u:
            continue
        try:
            p = urlparse(u)
            if p.netloc:
                allowed_hosts.add(p.netloc.lower().split(":")[0])
                allowed_paths.append(p.path or "/")
        except Exception:
            continue
    if not allowed_hosts:
        return [c for c in cookies if c.get("name")]

    filtered: list[dict] = []
    for c in cookies:
        name = c.get("name")
        if not name:
            continue
        domain = (c.get("domain") or "").lstrip(".").lower()
        if domain and not any(h == domain or h.endswith("." + domain) for h in allowed_hosts):
            continue
        cookie_path = c.get("path") or "/"
        if not any(_path_matches(cookie_path, rp) for rp in allowed_paths):
            continue
        filtered.append(c)
    return filtered


def build_cookie_jar(cookies: list[dict]):
    """Сборка RequestsCookieJar из браузерных cookies с учётом domain/path/secure/expiry.

    Использование jar в ``requests.get(url, cookies=jar)`` заставляет Requests
    самостоятельно применять корректную cookie-политику (domain + path + secure)
    к каждому конкретному URL вместо ручной склейки заголовка Cookie.
    """
    import requests

    jar = requests.cookies.RequestsCookieJar()
    for c in cookies:
        name = c.get("name")
        if not name:
            continue
        try:
            jar.set(
                name,
                c.get("value", ""),
                domain=c.get("domain") or None,
                path=c.get("path") or "/",
                secure=bool(c.get("secure")),
                expires=c.get("expiry"),
            )
        except Exception:
            continue
    return jar


def normalize_url(url: str) -> str:
    """Любая ссылка VK приводится к каноническому embed-виду с oid/id."""
    match = re.search(r"/video(-?\d+)_(\d+)", urlparse(url).path)
    if not match:
        return url
    return f"https://vkvideo.ru/video_ext.php?oid={match.group(1)}&id={match.group(2)}"


def codec_is(track_codecs: str, families: tuple[str, ...]) -> bool:
    """Сопоставление кодека по fourcc (первый компонент)."""
    fourcc = track_codecs.split(".")[0].strip().lower()
    return fourcc in families


def free_disk_gb(path: Path | str = ".") -> float:
    """Свободное место на диске в ГБ."""
    try:
        free = shutil.disk_usage(str(path)).free
        return free / 1024**3
    except Exception:
        return 0.0
