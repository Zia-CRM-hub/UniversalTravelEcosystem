import asyncio
import random
import re
import time

import httpx

from ..core.errors import ProviderError, ProviderUnavailable
from ..core.observability import PROVIDER_CALLS, PROVIDER_LATENCY


class CircuitBreaker:
    """Stops hammering a supplier that is down. closed -> open -> half_open -> closed."""

    def __init__(self, threshold: int = 5, reset_s: float = 30, clock=time.monotonic):
        self.threshold, self.reset_s, self.clock = threshold, reset_s, clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.reset_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold or self.opened_at is not None:
            self.opened_at = self.clock()


def _message(r: httpx.Response) -> str:
    try:
        err = (r.json().get("errors") or [{}])[0]
        return err.get("message") or err.get("detail") or err.get("title") or f"HTTP {r.status_code}"
    except Exception:
        return f"HTTP {r.status_code}"


class ProviderHTTP:
    """httpx wrapper: timeouts, bounded retries for safe calls, breaker, metrics."""

    def __init__(self, name: str, base_url: str, *, timeout: float, retries: int, breaker: CircuitBreaker,
                 headers: dict | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 sleep=asyncio.sleep):
        self.name, self.retries, self.breaker, self._sleep = name, retries, breaker, sleep
        self.client = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers or {}, transport=transport)

    async def request(self, method: str, path: str, *, retry: bool = False, **kw) -> dict:
        """`retry=True` only for idempotent/safe calls (searches, GETs). Orders must pass False."""
        if not self.breaker.allow():
            PROVIDER_CALLS.labels(self.name, "circuit_open").inc()
            raise ProviderUnavailable(self.name, "circuit open")
        attempts = 1 + (self.retries if retry else 0)
        last: ProviderError | None = None
        for i in range(attempts):
            t0 = time.perf_counter()
            try:
                r = await self.client.request(method, path, **kw)
            except httpx.TransportError as e:  # includes timeouts
                last = ProviderError(self.name, f"network error: {type(e).__name__}", retryable=True)
                PROVIDER_CALLS.labels(self.name, "network_error").inc()
            else:
                PROVIDER_LATENCY.labels(self.name).observe(time.perf_counter() - t0)
                if r.status_code < 400:
                    self.breaker.success()
                    PROVIDER_CALLS.labels(self.name, "ok").inc()
                    return r.json() if r.content else {}
                if r.status_code == 429 or r.status_code >= 500:
                    last = ProviderError(self.name, _message(r), status=r.status_code, retryable=True)
                    PROVIDER_CALLS.labels(self.name, "server_error").inc()
                else:  # our request was bad; the supplier itself is healthy
                    self.breaker.success()
                    PROVIDER_CALLS.labels(self.name, "client_error").inc()
                    raise ProviderError(self.name, _message(r), status=r.status_code, client_error=True)
            if i < attempts - 1:
                await self._sleep(min(2.0, 0.2 * 2**i) + random.random() * 0.1)
        self.breaker.failure()
        assert last is not None
        raise last

    async def aclose(self) -> None:
        await self.client.aclose()


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_duration_min(iso: str | None) -> int:
    """ISO-8601 duration ('PT2H30M', 'P1DT1H') to minutes; 0 if absent/unparseable."""
    m = _DUR.match(iso or "")
    if not m:
        return 0
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi
