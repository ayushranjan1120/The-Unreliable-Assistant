"""Two-tier cache (ADR-3, ADR-6). L1: per-user exact (spec key). L2: tenant-scoped, ``shareable`` answers only.

Isolation: every key goes through ``src.keys``; the stored value carries ``tenant_id`` and is re-asserted on read
(mismatch → TENANT_VIOLATION + miss). Redis failure → per-worker in-memory LRU (5 s TTL). Single-flight per key
(worker-local) prevents stampedes; TTL is jittered (300 s ± 45 s).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

from src import keys, telemetry
from src.errors import Code, ToolError

LRU_TTL_S, LRU_MAX = 5.0, 5000


class TwoTierCache:
    """Async cache facade."""

    def __init__(self, redis: Any | None, policy_version: str = "p1", ttl: int = 300, jitter: int = 45) -> None:
        self.r, self.policy, self.ttl, self.jitter = redis, policy_version, ttl, jitter
        self._lru: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._flight: dict[str, asyncio.Future] = {}
        self.stats = {"l1_hit": 0, "l2_hit": 0, "miss": 0, "redis_errors": 0, "violations": 0, "stores_l1": 0, "stores_l2": 0}
        self.l2_enabled = True            # lever D1

    def _lru_get(self, k: str) -> str | None:
        v = self._lru.get(k)
        if v and time.monotonic() < v[0]:
            return v[1]
        self._lru.pop(k, None)
        return None

    def _lru_put(self, k: str, val: str) -> None:
        self._lru[k] = (time.monotonic() + LRU_TTL_S, val)
        if len(self._lru) > LRU_MAX:
            self._lru.popitem(last=False)

    async def _get(self, k: str) -> str | None:
        if self.r is not None:
            try:
                return await asyncio.wait_for(self.r.get(k), 0.05)
            except Exception:
                self.stats["redis_errors"] += 1
        return self._lru_get(k)

    async def _set(self, k: str, val: str) -> None:
        self._lru_put(k, val)
        if self.r is not None:
            try:
                await asyncio.wait_for(self.r.set(k, val, ex=keys.ttl(self.ttl, self.jitter)), 0.05)
            except Exception:
                self.stats["redis_errors"] += 1

    def _check(self, raw: str | None, tenant: str, user: str | None) -> dict | None:
        if raw is None:
            return None
        d = json.loads(raw)
        if d.get("tenant_id") != tenant or (user is not None and d.get("user_id") != user):   # on-read assertion
            self.stats["violations"] += 1
            raise ToolError(Code.TENANT_VIOLATION, "cache entry tenant mismatch")
        return d["payload"]

    async def lookup(self, tenant: str, user: str, query: str) -> tuple[str, dict] | None:
        """Return ``('L1'|'L2', payload)`` or None."""
        with telemetry.span("cache.lookup"):
            k1 = keys.l1_key(tenant, user, query)
            p = self._check(await self._get(k1), tenant, user)
            if p is not None:
                self.stats["l1_hit"] += 1
                return "L1", p
            if self.l2_enabled:
                p = self._check(await self._get(keys.l2_key(tenant, self.policy, query)), tenant, None)
                if p is not None:
                    self.stats["l2_hit"] += 1
                    return "L2", p
            self.stats["miss"] += 1
            return None

    async def store(self, tenant: str, user: str, query: str, payload: dict, shareable: bool) -> None:
        """Store under L1 always; under L2 only if ``shareable`` (no user-specific data, low risk)."""
        await self._set(keys.l1_key(tenant, user, query), json.dumps({"tenant_id": tenant, "user_id": user, "payload": payload}, default=str))
        self.stats["stores_l1"] += 1
        if shareable and self.l2_enabled:
            await self._set(keys.l2_key(tenant, self.policy, query), json.dumps({"tenant_id": tenant, "payload": payload}, default=str))
            self.stats["stores_l2"] += 1

    async def single_flight(self, key: str, fn):
        """Coalesce concurrent identical misses within this worker."""
        if key in self._flight:
            return await asyncio.shield(self._flight[key])
        fut = asyncio.get_running_loop().create_future()
        self._flight[key] = fut
        try:
            res = await fn()
            fut.set_result(res)
            return res
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()                 # mark retrieved
            raise
        finally:
            self._flight.pop(key, None)

    def hit_ratio(self) -> float | None:
        """(L1+L2 hits)/(lookups) or None if no lookups."""
        n = self.stats["l1_hit"] + self.stats["l2_hit"] + self.stats["miss"]
        return None if n == 0 else 100.0 * (self.stats["l1_hit"] + self.stats["l2_hit"]) / n
