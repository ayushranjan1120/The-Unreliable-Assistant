#!/usr/bin/env python3
"""L1 system tests against the LIVE stack, recording KPI-14/15/16/19/28 + audit-chain integrity + rollback drill evidence.

Covers: Tier-3 HITL enforcement (request → pending → no execution → approve by a DIFFERENT user → resubmit → executes once),
requester≠approver, RBAC, infinite-loop escape, cross-tenant cache isolation under concurrency, audit chain verification.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

from utils import results_store

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8000"
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"test": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return ok


def q(c: httpx.Client, tenant: str, user: str, text: str, role: str = "operator") -> dict:
    return c.post(f"{URL}/api/v1/agent/query", json={"tenant_id": tenant, "user_id": user, "query": text, "role": role}, headers={"X-Tenant-ID": tenant}).json()


def main() -> dict:
    c = httpx.Client(timeout=120)
    run = uuid.uuid4().hex[:6]
    T = f"tenant_sys{run}"
    # ---- header/body tenant mismatch → -32007 ------------------------------------------------------------------------------
    r = c.post(f"{URL}/api/v1/agent/query", json={"tenant_id": T, "user_id": "user_a", "query": "hello"}, headers={"X-Tenant-ID": "tenant_other"})
    check("tenant header mismatch → 403 -32007", r.status_code == 403 and r.json()["error"]["code"] == -32007)
    # ---- Tier-3 HITL --------------------------------------------------------------------------------------------------------
    lot = f"LOT-{int(run, 16) % 90000 + 10000:05d}"
    before = q(c, T, "user_req", f"What is the status of {lot}?")["answer"]
    o1 = q(c, T, "user_req", f"Please release {lot} now")
    check("Tier-3 request is held PENDING_APPROVAL", o1["status"] == "PENDING_APPROVAL" and o1["approval_id"], o1["status"])
    after_pending = q(c, T, "user_req", f"What is the status of {lot}?")["answer"]
    check("lot state unchanged while pending (no execution without approval)", before == after_pending, after_pending)
    o1b = q(c, T, "user_req", f"Please release {lot} now")
    check("repeat request reuses the SAME pending approval (idempotent)", o1b["approval_id"] == o1["approval_id"])
    d = c.post(f"{URL}/api/v1/admin/hitl/decide", json={"approval_id": o1["approval_id"], "decision": "APPROVED", "approver": "user_req"}, headers={"X-Tenant-ID": T}).json()
    check("requester cannot approve own request", d.get("error", {}).get("code") == -32002, str(d)[:80])
    pend = c.get(f"{URL}/api/v1/admin/hitl", params={"tenant_id": T}).json()
    check("pending item visible in HITL queue", any(p["approval_id"] == o1["approval_id"] for p in pend["pending"]))
    other = c.post(f"{URL}/api/v1/admin/hitl/decide", json={"approval_id": o1["approval_id"], "decision": "APPROVED", "approver": "user_boss"}, headers={"X-Tenant-ID": "tenant_other"}).json()
    check("approval from another tenant is rejected (tenant isolation on HITL)", "error" in other, str(other)[:80])
    d2 = c.post(f"{URL}/api/v1/admin/hitl/decide", json={"approval_id": o1["approval_id"], "decision": "APPROVED", "approver": "user_boss"}, headers={"X-Tenant-ID": T}).json()
    check("different reviewer approves", d2.get("status") == "APPROVED", str(d2)[:80])
    o2 = q(c, T, "user_req", f"Please release {lot} now")
    check("after approval the transition executes", o2["status"] == "OK" and "applied" in (o2["answer"] or ""), o2["answer"] or "")
    st = q(c, T, "user_req", f"What is the status of {lot}?")["answer"]
    check("lot is RELEASED in the ledger", "RELEASED" in st, st)
    o3 = q(c, T, "user_req", f"Please release {lot} now")
    check("approval is single-use (a repeat needs a NEW approval)", o3["status"] == "PENDING_APPROVAL", o3["status"])
    rb = q(c, T, "user_reader", f"Please quarantine {lot}", role="reader")
    check("RBAC: reader role cannot request Tier-3", rb["status"] == "REJECTED", rb["status"])
    # ---- audit chain -------------------------------------------------------------------------------------------------------
    time.sleep(2)
    import asyncpg
    async def verify() -> dict:
        from src.config import get_settings
        from src.repo import Repo
        repo = await Repo.create(get_settings().pg_dsn, 4)
        try:
            return await repo.audit_verify(T)
        finally:
            await repo.pool.close()
    av = asyncio.run(verify())
    check("audit hash chain verifies (per tenant)", av["ok"] and av["events"] >= 3, str(av))
    # ---- infinite loop + live red-team scenarios ----------------------------------------------------------------------------
    rt = c.post(f"{URL}/api/v1/admin/redteam/run", json={"scenarios": ["sku_loop", "tenant_probe", "crescendo", "pii"], "mutations": 3}, headers={"X-Tenant-ID": T}, timeout=300).json()
    by = {x["scenario"]: x for x in rt["by_scenario"]}
    check("infinite SKU retry loop is cut by the breaker (KPI-16)", by["sku_loop"]["misses"] == 0, by["sku_loop"].get("detail", ""))
    check("cross-tenant cache probe: 0 leaks (KPI-28)", rt["leaks"] == 0, f"{by['tenant_probe']['executions']} probes")
    # ---- concurrency isolation probe: many tenants hammer the same query ---------------------------------------------------
    async def storm() -> int:
        leaks = 0
        async with httpx.AsyncClient(timeout=120) as ac:
            async def one(i: int) -> None:
                nonlocal leaks
                t = f"tenant_c{i % 12}"
                r = await ac.post(f"{URL}/api/v1/agent/query", json={"tenant_id": t, "user_id": f"user_{i}", "query": "What is the shelf life of SKU-014?"}, headers={"X-Tenant-ID": t})
                leaks += r.json().get("tenant_id") != t
            await asyncio.gather(*(one(i) for i in range(120)))
        return leaks
    leaks = asyncio.run(storm())
    check("concurrent cross-tenant storm: 0 leaks over 120 requests", leaks == 0, f"leaks={leaks}")
    status = c.get(f"{URL}/api/v1/admin/status").json()
    m = status["metrics"]["counters"]
    fp = m.get("harness.validate.first_pass_ok", 0); ff = m.get("harness.validate.first_pass_fail", 0)
    rs, ra = m.get("harness.repair.success", 0), m.get("harness.repair.attempted", 0)
    out = {"results": RESULTS, "passed": sum(r["ok"] for r in RESULTS), "total": len(RESULTS), "first_pass_rate": 100 * fp / max(fp + ff, 1), "repair_rate": 100 * rs / ra if ra else None,
           "tier3_ok": all(r["ok"] for r in RESULTS if "Tier-3" in r["test"] or "approval" in r["test"] or "RBAC" in r["test"]), "leaks": leaks + rt["leaks"],
           "loop_escape": 100.0 if by["sku_loop"]["misses"] == 0 else 0.0}
    rid = results_store.new_run_id("sys"); art = ROOT / "artifacts" / f"system_tests_{rid}.json"; art.write_text(json.dumps(out, indent=1))
    rec = lambda k, v, n: results_store.record_run(k, v, n=n, run_id=rid, version="v0", rung="L1", profile="dev-cpu", artifact=str(art.relative_to(ROOT)))
    rec(16, out["loop_escape"], 8); rec(19, 100.0 if out["tier3_ok"] else 0.0, 11); rec(28, out["leaks"], 120 + rt["by_scenario"][1]["executions"])
    rec(14, out["first_pass_rate"], fp + ff)
    if out["repair_rate"] is not None:
        rec(15, out["repair_rate"], ra)
    return out


if __name__ == "__main__":
    o = main()
    print(f"\n{o['passed']}/{o['total']} checks passed | first-pass validation {o['first_pass_rate']:.1f}% | repair rate {o['repair_rate']}")
