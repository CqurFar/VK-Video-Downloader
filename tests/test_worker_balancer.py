"""Тесты балансировщика воркеров."""

from vk_downloader.download.worker_balancer import WorkerBalancer


def test_initial_clamped_to_bounds():
    balancer = WorkerBalancer(initial=999, maximum=64, minimum=8)
    assert balancer.value == 64


def test_grow_after_success_streak():
    balancer = WorkerBalancer(
        initial=8, maximum=64, minimum=8, grow_after=3, step=4
    )
    for _ in range(3):
        balancer.success()
    assert balancer.value == 12


def test_failure_halves_current():
    balancer = WorkerBalancer(initial=32, maximum=64, minimum=8)
    balancer.failure()
    assert balancer.value == 16


def test_freeze_halves_once_and_stops_changes():
    balancer = WorkerBalancer(initial=32, maximum=64, minimum=8)
    balancer.freeze()
    frozen_value = balancer.value
    balancer.success()
    balancer.failure()
    assert balancer.value == frozen_value
