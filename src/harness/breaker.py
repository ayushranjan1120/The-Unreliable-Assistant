"""Idempotent circuit breaker on ``(tool_name, canonical_args_hash)`` (design Gate 9.1, ADR-4).

Trips at ≥ 2 failures. State is process-local (so loop protection survives a Redis outage — FMEA invariant)
and mirrored best-effort to tenant-prefixed Redis keys so workers share trips.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from src import keys

REMEDIATION = ("This request kept failing with identical arguments, so it was stopped to protect the system. "
               "Please check the entity id (expected formats SKU-123 or LOT-12345) and rephrase, or contact support.")


def canonical_args_hash(args: dict[str, Any]) -> str:
    """Stable hash of arguments excluding per-call fields (idempotency_key, trace_id)."""
    clean = {k: v for k, v in args.items() if k not in ("idempotency_key", "trace_id")}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:32]


class CircuitBreaker:
    """Counts failures per (tenant, tool, args_hash); open when count ≥ ``max_failures``."""

    def __init__(self, redis: Any | None = None, max_failures: int = 2, ttl_s: int = 60) -> None:
        self.redis, self.max_failures, self.ttl_s = redis, max_failures, ttl_s
        self._mem: dict[tuple[str, str, str], tuple[int, float]] = {}

    def _get(self, k: tuple[str, str, str]) -> int:
        n, exp = self._mem.get(k, (0, 0.0))
        if exp and time.monotonic() > exp:
            self._mem.pop(k, None)
            return 0
        return n

    async def is_open(self, tenant: str, tool: str, h: str) -> bool:
        """True if this exact call has already failed ``max_failures`` times."""
        k = (tenant, tool, h)
        if self._get(k) >= self.max_failures:
            return True
        if self.redis is not None:
            try:
                v = await self.redis.get(keys.breaker_key(tenant, tool, h))
                if v is not None and int(v) >= self.max_failures:
                    return True
            except Exception:  # Redis down → memory-only (never lose loop protection)
                pass
        return False

    async def record_failure(self, tenant: str, tool: str, h: str) -> int:
        """Record a failure; returns the new count."""
        k = (tenant, tool, h)
        n = self._get(k) + 1
        self._mem[k] = (n, time.monotonic() + self.ttl_s)
        if self.redis is not None:
            try:
                rk = keys.breaker_key(tenant, tool, h)
                n = max(n, int(await self.redis.incr(rk)))
                await self.redis.expire(rk, self.ttl_s)
            except Exception:
                pass
        return n

    def entries(self, tenant: str | None = None) -> list[dict]:
        """Open/accumulating entries for the admin UI."""
        return [{"tenant": t, "tool": tool, "args_hash": h, "failures": n, "open": n >= self.max_failures}
                for (t, tool, h), (n, exp) in self._mem.items() if (tenant is None or t == tenant) and time.monotonic() <= exp]

    async def reset(self, tenant: str | None = None) -> None:
        """Clear breaker state (admin)."""
        for k in [k for k in self._mem if tenant is None or k[0] == tenant]:
            self._mem.pop(k, None)
        if self.redis is not None:
            try:
                async for rk in self.redis.scan_iter(match=keys.breaker_pattern(tenant)):
                    await self.redis.delete(rk)
            except Exception:
                pass
