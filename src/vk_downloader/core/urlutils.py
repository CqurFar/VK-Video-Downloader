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


def filter_cookies(
    cookies: list[dict],
    *urls: str | None,
) -> list[dict]:
    """Фильтрация cookies по domain/path — не шлём лишние session cookies на CDN."""
    if not cookies:
        return []
    allowed_hosts: set[str] = set()
    for u in urls:
        if not u:
            continue
        try:
            p = urlparse(u)
            if p.netloc:
                allowed_hosts.add(p.netloc.lower().split(":")[0])
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
        filtered.append(c)
    return filtered


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
