"""Windows-специфика путей: длинные пути и скрытые атрибуты."""

import contextlib
import ctypes
import os


class FSPaths:
    """УТИЛИТЫ ПУТЕЙ ДЛЯ ОБХОДА ЛИМИТА ДЛИНЫ WINDOWS MAX_PATH"""

    # Абсолютный путь с префиксом \\?\, снимающим лимит 260 символов
    @staticmethod
    def long(path) -> str:
        absolute = os.path.abspath(path)
        if os.name == "nt" and not absolute.startswith("\\\\?\\"):
            return "\\\\?\\" + absolute
        return absolute

    # Скрытая папка
    @staticmethod
    def hide(path) -> None:
        if os.name != "nt":
            return
        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.SetFileAttributesW(os.path.abspath(path), 0x02)


# === Пример ===
# from vk_downloader.ui.fs_paths import FSPaths
# Path(FSPaths.long("downloaded/file.mkv")).write_bytes(b"data")
