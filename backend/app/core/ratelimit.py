"""Fixed-window rate limiting with a pluggable store.

In-process by default (fine for one API replica). Set REDIS_URL to share the counters across
replicas, which is required before scaling the API out horizontally. If Redis is unreachable we
fail OPEN (allow the request): an outage of the limiter must not take the product down.
"""
import logging
import time
from typing import Protocol

log = logging.getLogger(__name__)


class Limiter(Protocol):
    async def hit(self, key: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
        """Count one event. Returns (allowed, retry_after_seconds)."""

    async def aclose(self) -> None: ...


class MemoryLimiter:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._windows: dict[str, tuple[int, int]] = {}

    async def hit(self, key, limit, window_s=60):
        now = self._clock()
        win = int(now // window_s)
        w, count = self._windows.get(key, (win, 0))
        if w != win:
            count = 0
        count += 1
        if len(self._windows) > 50_000:  # bound memory under key churn
            self._windows = {k: v for k, v in self._windows.items() if v[0] == win}
        self._windows[key] = (win, count)
        return count <= limit, max(1, int((win + 1) * window_s - now))

    async def aclose(self):
        return None


class RedisLimiter:
    def __init__(self, client):
        self.r = client

    async def hit(self, key, limit, window_s=60):
        k = f"rl:{key}"
        try:
            count = await self.r.incr(k)
            if count == 1:
                await self.r.expire(k, window_s)
            retry = await self.r.ttl(k) if count > limit else 0
            return count <= limit, max(1, retry)
        except Exception:
            log.warning("rate limiter store unavailable; allowing request", exc_info=True)
            return True, 1

    async def aclose(self):
        await self.r.aclose()


def build_limiter(redis_url: str | None) -> Limiter:
    if not redis_url:
        return MemoryLimiter()
    import redis.asyncio as aioredis

    return RedisLimiter(aioredis.from_url(redis_url, socket_timeout=1, socket_connect_timeout=1))
