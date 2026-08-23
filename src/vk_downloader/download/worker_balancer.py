class WorkerBalancer:
    """БАЛАНСИРОВЩИК ПОТОКОВ"""

    # Начальное значение берётся по числу сегментов, но в границах [minimum, maximum]
    def __init__(
        self,
        initial: int,
        maximum: int,
        minimum: int = 8,
        grow_after: int = 16,
        step: int = 8,
    ):
        self.maximum = max(maximum, 1)
        self.minimum = max(minimum, 1)
        if self.minimum > self.maximum:
            self.minimum = self.maximum
        self.current = max(min(initial, self.maximum), self.minimum)
        self.grow_after = grow_after
        self.step = step
        self._success_streak = 0
        self.frozen = False

    # Заморозка на финише: однократное снижение вдвое и отказ от изменений
    def freeze(self) -> None:
        self.frozen = True
        self.current = max(self.current // 2, self.minimum)

    # Текущее число worker
    @property
    def value(self) -> int:
        return self.current

    # Отчёт об успешной задаче: чистая серия наращивает workers
    def success(self) -> None:
        if self.frozen:
            return
        self._success_streak += 1
        if self._success_streak >= self.grow_after and self.current < self.maximum:
            self.current = min(self.current + self.step, self.maximum)
            self._success_streak = 0

    # Отчёт об ошибке: мгновенное снижение вдвое и сброс серии
    def failure(self) -> None:
        if self.frozen:
            return
        self._success_streak = 0
        self.current = max(self.current // 2, self.minimum)


# === Пример ===
# balancer = WorkerBalancer(initial=16, maximum=64)
# for _ in range(100): balancer.success()
# print(balancer.value)
