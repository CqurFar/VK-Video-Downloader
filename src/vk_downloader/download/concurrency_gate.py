import threading


class ConcurrencyGate:
    """ЛИМИТЕР ПАРАЛЛЕЛИЗМА БЕЗ РАЗРЫВА СОЕДИНЕНИЙ"""

    # Лимит меняется только для новых задач: активные загрузки никогда не прерываются
    def __init__(self, initial: int):
        self._limit = max(initial, 1)
        self._active = 0
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    # Установка нового лимита с пробуждением ожидающих задач
    def set(self, value: int) -> None:
        with self._cond:
            self._limit = max(value, 1)
            self._cond.notify_all()

    # Захват слота под задачу
    def __enter__(self):
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1
        return self

    # Освобождение слота
    def __exit__(self, *exc_info) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify()


# === Пример ===
# gate = ConcurrencyGate(8)
# gate.set(16)
