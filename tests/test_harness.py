"""L1-style harness tests: validation/repair (KPI-14/15), RBAC, tenant, idempotency, HITL, breaker (KPI-16/19)."""
import time
import uuid

import pytest

from src import telemetry
from src.contracts.models import LotStatusIn, SkuLookupIn, WorkflowTransitionIn
from src.errors import Code, ToolError
from src.harness.breaker import CircuitBreaker
from src.harness.harness import IdemStore, ReqCtx, ToolExecutionHarness, ToolSpec


class FakeHitl:
    def __init__(self): self.approved = set(); self.created = []
    async def check_or_create(self, rc, tool, args_hash, args):
        if (tool, args_hash) in self.approved:
            return str(uuid.uuid4())
        self.created.append((tool, args_hash))
        raise ToolError(Code.HITL_PENDING, "pending approval", {"approval_id": "x"})


def make(hitl=None):
    calls = {"n": 0, "fail": False}
    async def sku(m, rc):
        calls["n"] += 1
        if calls["fail"]: raise RuntimeError("db down")
        return {"sku": m.sku, "status": "ACTIVE", "trace_id": "drop-me"}
    async def lot(m, rc): return {"lot": m.lot, "status": "RELEASED"}
    async def trans(m, rc): calls["n"] += 1; return {"entity": m.entity, "transition": m.transition, "done": True}
    reg = {"sku_lookup": ToolSpec("sku_lookup", "state-store", "TIER_1_READ", SkuLookupIn, sku),
           "lot_status": ToolSpec("lot_status", "state-store", "TIER_1_READ", LotStatusIn, lot),
           "workflow_transition": ToolSpec("workflow_transition", "state-store", "TIER_3_MUTATE", WorkflowTransitionIn, trans)}
    h = ToolExecutionHarness(reg, CircuitBreaker(), IdemStore(), hitl)
    return h, calls


def rc(role="operator", tenant="tenant_1"):
    return ReqCtx(tenant, "user_1", role, str(uuid.uuid4()), "a" * 32)


async def test_first_pass_and_autorepair_counters():
    telemetry.METRICS.reset(); h, _ = make()
    o = await h.call("sku_lookup", {"sku": "SKU-099"}, rc())
    assert o.repairs == [] and "SKU-099" in o.text
    o = await h.call("sku_lookup", {"sku": "SKU99"}, rc())
    assert o.repairs == ["sku:SKU99->SKU-099"]
    c = telemetry.METRICS.counters
    assert c["harness.validate.first_pass_ok"] == 1 and c["harness.repair.success"] == 1
    o = await h.call("lot_status", {"lot": "lot 44"}, rc())
    assert "LOT-00044" in o.text


async def test_unrepairable_is_validation_error_not_guess():
    h, _ = make()
    with pytest.raises(ToolError) as e:
        await h.call("sku_lookup", {"sku": "SKU-99999"}, rc())
    assert e.value.code == Code.VALIDATION_FAILED


async def test_boilerplate_stripped_from_observation():
    h, _ = make()
    o = await h.call("sku_lookup", {"sku": "SKU-001"}, rc())
    assert "drop-me" not in o.text and o.tokens <= 180


async def test_tenant_mismatch_and_rbac():
    h, _ = make()
    with pytest.raises(ToolError) as e:
        await h.call("sku_lookup", {"sku": "SKU-001", "tenant_id": "tenant_2"}, rc())
    assert e.value.code == Code.TENANT_VIOLATION
    with pytest.raises(ToolError) as e:
        await h.call("workflow_transition", {"entity": "LOT-00044", "transition": "RELEASE", "user_id": "user_1"}, rc(role="reader"))
    assert e.value.code == Code.RBAC_DENIED


async def test_tier3_never_executes_without_approval():
    hitl = FakeHitl(); h, calls = make(hitl)
    args = {"entity": "LOT-00044", "transition": "RELEASE", "user_id": "user_1"}
    for _ in range(3):
        with pytest.raises(ToolError) as e:
            await h.call("workflow_transition", args, rc())
        assert e.value.code == Code.HITL_PENDING
    assert calls["n"] == 0                       # KPI-19: handler never reached
    (tool, ah) = hitl.created[0]; hitl.approved.add((tool, ah))
    o = await h.call("workflow_transition", args, rc())
    assert calls["n"] == 1 and "done" in o.text


async def test_idempotent_replay():
    h, calls = make(); ctx = rc()
    a = await h.call("sku_lookup", {"sku": "SKU-005"}, ctx)
    b = await h.call("sku_lookup", {"sku": "SKU-005"}, ctx)
    assert b.replayed and calls["n"] == 1 and a.text == b.text


async def test_breaker_trips_on_identical_failures_incl_typo_retries():  # KPI-16 (loop escape)
    h, calls = make(); calls["fail"] = True; ctx = rc()
    results = []
    for _ in range(6):
        try:
            await h.call("sku_lookup", {"sku": "SKU99"}, ctx)
        except ToolError as e:
            results.append(e.code)
    assert Code.BREAKER_OPEN in results
    assert calls["n"] == 2                       # handler executed exactly max_failures times, then blocked
    assert results.index(Code.BREAKER_OPEN) == 2


async def test_timeout_is_coded_and_counts_as_failure():
    h, _ = make()
    import asyncio
    async def slow(m, r): await asyncio.sleep(1)
    h.registry["sku_lookup"] = ToolSpec("sku_lookup", "state-store", "TIER_1_READ", SkuLookupIn, slow)
    t = time.perf_counter()
    with pytest.raises(ToolError) as e:
        await h.call("sku_lookup", {"sku": "SKU-002"}, rc())
    assert e.value.code == Code.TOOL_TIMEOUT and time.perf_counter() - t < 0.4
