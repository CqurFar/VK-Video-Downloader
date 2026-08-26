"""Тесты лимитера параллелизма."""

import threading

from vk_downloader.download.concurrency_gate import ConcurrencyGate


def test_acquire_release_is_clean():
    gate = ConcurrencyGate(2)
    with gate:
        assert gate.limit == 2


def test_set_raises_limit():
    gate = ConcurrencyGate(1)
    gate.set(4)
    assert gate.limit == 4


def test_concurrent_entries_respect_limit():
    gate = ConcurrencyGate(2)
    active = {"n": 0}
    peak = {"n": 0}
    lock = threading.Lock()

    def worker():
        with gate:
            with lock:
                active["n"] += 1
                peak["n"] = max(peak["n"], active["n"])
            threading.Event().wait(0.01)
            with lock:
                active["n"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak["n"] <= 2
