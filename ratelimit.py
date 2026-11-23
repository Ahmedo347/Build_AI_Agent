"""Minimum-interval rate limiter applied between outbound model requests.

Free tiers are usually limited per minute, and one agent task issues several
requests back to back. Sleeping between *tasks* does not help; the wait has to
happen between *requests*.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._clock = clock
        self._sleep = sleep
        self._last_request: float | None = None
        self.total_wait = 0.0
        self.waits = 0

    def wait(self) -> float:
        """Block until the next request is allowed. Returns the seconds slept."""
        if self.min_interval <= 0:
            return 0.0

        now = self._clock()
        if self._last_request is None:
            self._last_request = now
            return 0.0

        delay = self.min_interval - (now - self._last_request)
        if delay <= 0:
            self._last_request = now
            return 0.0

        self._sleep(delay)
        self._last_request = self._clock()
        self.total_wait += delay
        self.waits += 1
        return delay
