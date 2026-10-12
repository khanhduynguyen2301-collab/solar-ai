"""Owner: Duy. A small in-memory request limiter: at most N requests per client per minute.

Good enough for one process. It keeps no state across restarts and is not
shared between workers, so a multi-worker deployment needs a shared store.
"""

from collections import defaultdict, deque
import threading
import time
from typing import Callable


WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding one-minute window per client key. A limit of 0 turns the limiter off."""

    def __init__(self, limit_per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if limit_per_minute < 0:
            raise ValueError("limit_per_minute cannot be negative")
        self._limit, self._clock = limit_per_minute, clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> float | None:
        """Record one request. Returns None when it is allowed, otherwise the seconds until the next one would be."""
        if self._limit == 0:
            return None
        now = self._clock()
        with self._lock:
            recent = self._requests[client]
            while recent and now - recent[0] >= WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= self._limit:
                return WINDOW_SECONDS - (now - recent[0])
            recent.append(now)
            return None
