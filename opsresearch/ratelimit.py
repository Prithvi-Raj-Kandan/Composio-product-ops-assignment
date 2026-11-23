"""One shared limiter for extraction and verification calls."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    def __init__(
        self,
        min_interval_s: float = 0.4,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval_s
        if delay:
            self._sleep(delay)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)


def with_retries(fn: Callable[[], T], limiter: RateLimiter, *, attempts: int = 4) -> T:
    delay = 0.5
    last: Exception | None = None
    for attempt in range(attempts):
        limiter.wait()
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt == attempts - 1:
                break
            limiter.sleep(delay)
            delay *= 2
    assert last is not None
    raise last


_SHARED: RateLimiter | None = None
_SHARED_LOCK = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            raw = os.environ.get("OPSRESEARCH_MIN_INTERVAL_S", "0.4")
            _SHARED = RateLimiter(min_interval_s=float(raw))
        return _SHARED
