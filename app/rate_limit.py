from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Protocol

from app.clock import Clock

MAX_FAILED_ATTEMPTS = 5
FAILURE_WINDOW = timedelta(minutes=15)


class RateLimitStore(Protocol):
    def failed_attempts(self, key: str, since: datetime) -> int: ...

    def record_failure(self, key: str, at: datetime) -> None: ...

    def clear(self, key: str) -> None: ...


class MemoryRateLimitStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._events: dict[str, list[datetime]] = {}

    def failed_attempts(self, key: str, since: datetime) -> int:
        with self._lock:
            kept = [moment for moment in self._events.get(key, []) if moment > since]
            self._events[key] = kept
            return len(kept)

    def record_failure(self, key: str, at: datetime) -> None:
        with self._lock:
            self._events.setdefault(key, []).append(at)

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class RateLimiter:
    def __init__(
        self,
        store: RateLimitStore,
        clock: Clock,
        *,
        max_attempts: int = MAX_FAILED_ATTEMPTS,
        window: timedelta = FAILURE_WINDOW,
    ) -> None:
        self._store = store
        self._clock = clock
        self._max_attempts = max_attempts
        self._window = window

    def is_blocked(self, key: str) -> bool:
        since = self._clock.now() - self._window
        return self._store.failed_attempts(key, since) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        self._store.record_failure(key, self._clock.now())

    def reset(self, key: str) -> None:
        self._store.clear(key)
