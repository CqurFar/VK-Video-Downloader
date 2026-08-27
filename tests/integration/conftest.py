"""Фикстуры для E2E replay-тестов: локальный CDN и анонимизированные MPD.

Тесты не трогают реальный VK: сегменты и манифест отдаёт локальный
``http.server``, а MPD — синтетические (без токенов/подписей).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fixtures import ABUSIVE_MPD, REPLAY_MPD  # noqa: F401


def _write_cdn_files(directory: Path) -> None:
    # init + 2 видео-сегмента + 2 аудио-сегмента: синтетические байты
    (directory / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom")
    (directory / "ainit.mp4").write_bytes(b"\x00\x00\x00\x20ftypmp42")
    for name, payload in (
        ("adapt-1.m4s", b"VIDEO-SEG-1"),
        ("adapt-2.m4s", b"VIDEO-SEG-2"),
        ("audio-1.m4s", b"AUDIO-SEG-1"),
        ("audio-2.m4s", b"AUDIO-SEG-2"),
    ):
        (directory / name).write_bytes(payload)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        target = self.server.cdn_root / path.lstrip("/")  # type: ignore[attr-defined]
        if target.is_file():
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # type: ignore[override]
        pass


@pytest.fixture
def replay_cdn(tmp_path):
    """Локальный CDN на 127.0.0.1:0, отдающий синтетические сегменты."""
    cdn_dir = tmp_path / "cdn"
    cdn_dir.mkdir()
    _write_cdn_files(cdn_dir)
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.cdn_root = cdn_dir  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
