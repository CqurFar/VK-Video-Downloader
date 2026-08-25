"""Точка входа CLI: ``uv run vk-dl url_01 url_02 --video best --audio best --format mkv``."""

import sys
import asyncio

from vk_downloader.core.downloader import VKMediaDownloader
from vk_downloader.settings import Config


def main() -> int:
    """Запуск загрузчика (Ctrl+C возвращает код 130 без traceback)."""
    try:
        return asyncio.run(VKMediaDownloader(Config()).run())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
