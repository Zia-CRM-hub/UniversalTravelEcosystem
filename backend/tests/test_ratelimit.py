import pytest

from app.core.ratelimit import MemoryLimiter, RedisLimiter, build_limiter

pytestmark = pytest.mark.anyio


async def test_memory_limiter_window_and_keys():
    t = [1000.0]
    lim = MemoryLimiter(clock=lambda: t[0])
    assert [(await lim.hit("a", 3))[0] for _ in range(4)] == [True, True, True, False]
    assert (await lim.hit("b", 3))[0] is True  # keys are independent
    _, retry = await lim.hit("a", 3)
    assert 1 <= retry <= 60
    t[0] += 60  # next window
    assert (await lim.hit("a", 3))[0] is True


async def test_redis_limiter_shares_counts_between_replicas():
    from fakeredis import FakeAsyncRedis

    shared = FakeAsyncRedis()
    replica_1, replica_2 = RedisLimiter(shared), RedisLimiter(shared)  # two API processes, one store
    assert (await replica_1.hit("t:ip", 2))[0] is True
    assert (await replica_2.hit("t:ip", 2))[0] is True
    allowed, retry = await replica_1.hit("t:ip", 2)
    assert allowed is False and 1 <= retry <= 60
    assert await shared.ttl("rl:t:ip") > 0  # window expires on its own


async def test_redis_outage_fails_open():
    class Down:
        async def incr(self, *a):
            raise ConnectionError("redis down")

    assert (await RedisLimiter(Down()).hit("k", 1))[0] is True  # limiter trouble must not take the API down


def test_build_limiter_selects_backend():
    assert isinstance(build_limiter(None), MemoryLimiter)
    assert isinstance(build_limiter("redis://localhost:6379/0"), RedisLimiter)
