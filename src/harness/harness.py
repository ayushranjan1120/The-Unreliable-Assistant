"""ToolExecutionHarness: the single gate every tool call passes (design §1.8, Gate 4).

Order: tenant check → strict validation → regex auto-repair → RBAC tier → Tier-3 HITL → breaker →
idempotency → dispatch with timeout → observation compression.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, ValidationError

from src import telemetry
from src.contracts.common import ROLE_MAX_TIER, TIER_RANK
from src.errors import Code, ToolError
from src.harness.breaker import CircuitBreaker, canonical_args_hash
from src.harness.compress import compress
from src.harness.repair import repair_args
from utils.token_calc import count

Handler = Callable[[BaseModel, "ReqCtx"], Awaitable[Any]]


@dataclass
class ReqCtx:
    """Per-request identity and budget."""
    tenant_id: str
    user_id: str
    role: str
    session_id: str
    trace_id: str
    deadline: float = field(default_factory=lambda: time.monotonic() + 0.450)
    goal_terms: set[str] = field(default_factory=set)

    def remaining_ms(self) -> float:
        """Milliseconds left in the total loop budget."""
        return (self.deadline - time.monotonic()) * 1000


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool."""
    name: str
    server: str
    tier: str
    model: type[BaseModel]
    handler: Handler
    timeout_ms: int = 120


@dataclass
class Observation:
    """Harness result: compressed text for the LLM + raw payload for verification."""
    tool: str
    text: str
    raw: Any
    tokens: int
    repairs: list[str]
    replayed: bool = False
    ms: float = 0.0


class HitlGate(Protocol):
    """Tier-3 approval gate (backed by mcp-state-store in production)."""
    async def check_or_create(self, rc: ReqCtx, tool: str, args_hash: str, args: dict) -> str: ...


class IdemStore:
    """Idempotency records: redis if available, else process memory. Keys are tenant-prefixed."""

    def __init__(self, redis: Any | None = None, ttl_s: int = 300) -> None:
        self.redis, self.ttl_s, self._mem = redis, ttl_s, {}

    async def get(self, tenant: str, key: str) -> dict | None:
        from src import keys
        k = keys.idem_key(tenant, key)
        if self.redis is not None:
            try:
                v = await self.redis.get(k)
                return json.loads(v) if v else None
            except Exception:
                pass
        return self._mem.get(k)

    async def put(self, tenant: str, key: str, rec: dict) -> None:
        from src import keys
        k = keys.idem_key(tenant, key)
        self._mem[k] = rec
        if len(self._mem) > 20000:
            self._mem.pop(next(iter(self._mem)))
        if self.redis is not None:
            try:
                await self.redis.set(k, json.dumps(rec, default=str), ex=self.ttl_s)
            except Exception:
                pass


def v4_from_hash(seed: str) -> str:
    """Deterministic RFC-4122 *version-4-format* UUID from a seed (replays map to the same idempotency key)."""
    b = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


class ToolExecutionHarness:
    """Validates, repairs, authorizes, de-duplicates, dispatches and compresses every tool call."""

    def __init__(self, registry: dict[str, ToolSpec], breaker: CircuitBreaker, idem: IdemStore, hitl: HitlGate | None,
                 obs_max_tokens: int = 180, per_step_timeout_ms: int = 120) -> None:
        self.registry, self.breaker, self.idem, self.hitl = registry, breaker, idem, hitl
        self.obs_max_tokens, self.per_step_timeout_ms = obs_max_tokens, per_step_timeout_ms
        self.m = telemetry.METRICS

    def _validate(self, spec: ToolSpec, args: dict) -> tuple[BaseModel, list[str]]:
        """Strict validate (JSON mode so UUID strings are legal); on failure try deterministic repair once."""
        try:
            m = spec.model.model_validate_json(json.dumps(args, default=str))
            self.m.inc("harness.validate.first_pass_ok")
            return m, []
        except ValidationError as first:
            self.m.inc("harness.validate.first_pass_fail")
            fixed, repairs = repair_args(args)
            if repairs:
                self.m.inc("harness.repair.attempted")
                try:
                    m = spec.model.model_validate_json(json.dumps(fixed, default=str))
                    self.m.inc("harness.repair.success")
                    return m, repairs
                except ValidationError:
                    self.m.inc("harness.repair.failed")
            raise ToolError(Code.VALIDATION_FAILED, f"{spec.name}: invalid arguments",
                            {"errors": [e["loc"] for e in first.errors()][:6]})

    async def call(self, tool: str, raw_args: dict, rc: ReqCtx) -> Observation:
        """Execute one tool call under all harness guarantees."""
        t0 = time.perf_counter()
        spec = self.registry.get(tool)
        if spec is None:
            raise ToolError(Code.RBAC_DENIED, f"unknown or unregistered tool '{tool}'")
        args = dict(raw_args)
        if args.get("tenant_id", rc.tenant_id) != rc.tenant_id:
            raise ToolError(Code.TENANT_VIOLATION, "tenant mismatch in tool arguments")
        args["tenant_id"] = rc.tenant_id
        args.setdefault("trace_id", rc.trace_id)
        ahash = canonical_args_hash({k: v for k, v in args.items() if k != "idempotency_key"})
        args.setdefault("idempotency_key", v4_from_hash(f"{rc.tenant_id}:{rc.session_id}:{tool}:{ahash}"))

        if await self.breaker.is_open(rc.tenant_id, tool, ahash):
            self.m.inc("breaker.open_hits")
            raise ToolError(Code.BREAKER_OPEN, "circuit breaker open for identical call", {"tool": tool})

        try:
            model, repairs = self._validate(spec, args)
        except ToolError:
            await self.breaker.record_failure(rc.tenant_id, tool, ahash)
            raise
        if repairs:  # repaired args hash differs from the typo'd one; count the typo'd one too
            ahash_fixed = canonical_args_hash({k: v for k, v in model.model_dump(mode="json").items() if k not in ("idempotency_key", "trace_id")})
        else:
            ahash_fixed = ahash

        if repairs and await self.breaker.is_open(rc.tenant_id, tool, ahash_fixed):  # typo'd retries map to the repaired call
            self.m.inc("breaker.open_hits")
            raise ToolError(Code.BREAKER_OPEN, "circuit breaker open for identical (repaired) call", {"tool": tool})

        if TIER_RANK[spec.tier] > ROLE_MAX_TIER.get(rc.role, 0):
            raise ToolError(Code.RBAC_DENIED, f"role '{rc.role}' may not call {spec.tier} tool '{tool}'")

        if spec.tier == "TIER_3_MUTATE":
            if self.hitl is None:  # fail closed
                raise ToolError(Code.HITL_PENDING, "HITL gate unavailable; Tier-3 refused (fail-closed)")
            approval_id = await self.hitl.check_or_create(rc, tool, ahash_fixed, model.model_dump(mode="json"))
            args["approval_id"] = approval_id  # only reached when approved; otherwise raises HITL_PENDING
            model = spec.model.model_validate_json(json.dumps(args, default=str))

        idem_key = str(args["idempotency_key"])
        prev = await self.idem.get(rc.tenant_id, idem_key)
        if prev:
            if prev["args_hash"] != ahash_fixed:
                raise ToolError(Code.IDEMPOTENCY_CONFLICT, "idempotency key reused with different arguments")
            self.m.inc("harness.idempotent_replay")
            return Observation(tool, prev["text"], prev["raw"], count(prev["text"]), repairs, True, 0.0)

        timeout = max(1.0, min(spec.timeout_ms, rc.remaining_ms())) / 1000        # spec.timeout_ms carries the per-step budget (default 120 ms × time_scale)
        try:
            raw = await asyncio.wait_for(spec.handler(model, rc), timeout=timeout)
        except asyncio.TimeoutError:
            await self.breaker.record_failure(rc.tenant_id, tool, ahash_fixed)
            raise ToolError(Code.TOOL_TIMEOUT, f"{tool} exceeded {timeout * 1000:.0f} ms")
        except ToolError:
            await self.breaker.record_failure(rc.tenant_id, tool, ahash_fixed)
            raise
        except Exception as e:  # tool-side failure
            await self.breaker.record_failure(rc.tenant_id, tool, ahash_fixed)
            raise ToolError(Code.TOOL_TIMEOUT, f"{tool} failed: {type(e).__name__}") from e

        text = compress(raw, rc.goal_terms, self.obs_max_tokens)
        await self.idem.put(rc.tenant_id, idem_key, {"args_hash": ahash_fixed, "text": text, "raw": _jsonable(raw)})
        ms = (time.perf_counter() - t0) * 1000
        self.m.observe(f"tool.{tool}", ms)
        return Observation(tool, text, raw, count(text), repairs, False, ms)


def _jsonable(x: Any) -> Any:
    if isinstance(x, BaseModel):
        return x.model_dump(mode="json")
    try:
        json.dumps(x)
        return x
    except TypeError:
        return json.loads(json.dumps(x, default=str))
