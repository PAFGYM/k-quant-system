"""Async rate limiter for API calls."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class AsyncRateLimiter:
    """Token-bucket rate limiter for async API calls.

    Args:
        max_calls: Maximum number of calls allowed in the time window.
        period_seconds: Time window in seconds.
    """

    def __init__(self, max_calls: int = 10, period_seconds: float = 1.0) -> None:
        self.max_calls = max_calls
        self.period = period_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a call is allowed under the rate limit."""
        async with self._lock:
            now = time.monotonic()

            # Remove timestamps outside the window
            while self._timestamps and now - self._timestamps[0] >= self.period:
                self._timestamps.popleft()

            if len(self._timestamps) >= self.max_calls:
                sleep_time = self.period - (now - self._timestamps[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            self._timestamps.append(time.monotonic())

    async def __aenter__(self) -> AsyncRateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        pass
