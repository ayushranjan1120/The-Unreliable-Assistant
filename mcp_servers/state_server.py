#!/usr/bin/env python3
"""mcp-state-store (Streamable HTTP :8081/mcp): facts, audit chain, HITL, lots, workflow transitions.

Tier-3 ``workflow_transition`` refuses to run without an approval id that the harness obtained by consuming an
APPROVED record (defence in depth: the server re-checks CONSUMED status).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.contracts.models import FactsGetIn, FactsPutIn, LotStatusIn, SkuLookupIn, WorkflowTransitionIn  # noqa: E402
from src.errors import Code, ToolError  # noqa: E402
from src.repo import Repo  # noqa: E402

S = get_settings()
mcp = FastMCP("state-store", host="127.0.0.1", port=8081, stateless_http=True, json_response=True)
_repo: Repo | None = None
_FACTS: dict | None = None


async def repo() -> Repo:
    """Lazy pool creation (db_pool bulkhead)."""
    global _repo
    if _repo is None:
        _repo = await Repo.create(S.pg_dsn, S.db_pool)
    return _repo


def _v(model, req: dict):
    return model.model_validate_json(json.dumps(req))


@mcp.tool()
async def lot_status(req: dict) -> dict:
    """TIER_1_READ: current status of a lot."""
    m = _v(LotStatusIn, req)
    return await (await repo()).lot_status(m.tenant_id, m.lot)


@mcp.tool()
async def sku_lookup(req: dict) -> dict:
    """TIER_1_READ: catalog record for a SKU (from the synthetic fact table)."""
    global _FACTS
    m = _v(SkuLookupIn, req)
    if _FACTS is None:
        _FACTS = json.loads((Path(__file__).resolve().parent.parent / "eval" / "data" / "facts.json").read_text())
    rec = _FACTS["skus"].get(m.sku)
    if rec is None:
        raise ToolError(Code.VALIDATION_FAILED, f"unknown sku {m.sku}")
    return {"sku": m.sku, **rec}


@mcp.tool()
async def facts_put(req: dict) -> dict:
    """TIER_2_COMPUTE: persist a verified session fact (must reference an evidence chunk)."""
    m = _v(FactsPutIn, req)
    await (await repo()).facts_put(m.tenant_id, m.session_id, m.fact, m.evidence_chunk_id)
    return {"ok": True}


@mcp.tool()
async def facts_get(req: dict) -> dict:
    """TIER_1_READ: verified facts for a session."""
    m = _v(FactsGetIn, req)
    return {"facts": await (await repo()).facts_get(m.tenant_id, m.session_id)}


@mcp.tool()
async def audit_append(req: dict) -> dict:
    """TIER_2_COMPUTE: append a hash-chained audit event (payload must be pre-redacted)."""
    return await (await repo()).audit_append(req["tenant_id"], req["event_type"], req["actor"], req.get("payload", {}))


@mcp.tool()
async def audit_verify(req: dict) -> dict:
    """TIER_1_READ: verify the tenant's audit chain."""
    return await (await repo()).audit_verify(req["tenant_id"])


@mcp.tool()
async def hitl_gate(req: dict) -> dict:
    """TIER_3 gate: create/check/consume an approval for ``(requester, tool, args_hash)``."""
    aid, status = await (await repo()).hitl_check_or_create(req["tenant_id"], req["requester"], req["tool"], req["args_hash"], req.get("args", {}))
    if status == "PENDING_APPROVAL":
        await (await repo()).audit_append(req["tenant_id"], "HITL_PENDING", req["requester"], {"approval_id": aid, "tool": req["tool"]})
    return {"approval_id": aid, "status": status}


@mcp.tool()
async def hitl_list(req: dict) -> dict:
    """TIER_1_READ: pending approvals for a tenant."""
    return {"pending": await (await repo()).hitl_list(req["tenant_id"])}


@mcp.tool()
async def hitl_decide(req: dict) -> dict:
    """Reviewer decision; requester ≠ approver is enforced in SQL layer."""
    r = await (await repo()).hitl_decide(req["tenant_id"], req["approval_id"], req["approver"], req["decision"])
    await (await repo()).audit_append(req["tenant_id"], f"HITL_{req['decision']}", req["approver"], {"approval_id": req["approval_id"]})
    return r


@mcp.tool()
async def workflow_transition(req: dict) -> dict:
    """TIER_3_MUTATE: apply a lot transition. Requires a CONSUMED approval (verified here again)."""
    m = _v(WorkflowTransitionIn, req)
    r = await repo()
    if m.approval_id is None or await r.hitl_status(m.tenant_id, str(m.approval_id)) != "CONSUMED":
        raise ToolError(Code.HITL_PENDING, "no consumed approval for this transition")      # fail-closed
    new = {"RELEASE": "RELEASED", "QUARANTINE": "QUARANTINED", "RECALL": "RECALLED", "APPROVE_BATCH": "APPROVED"}[m.transition]
    if m.entity.startswith("LOT-"):
        await r.lot_set(m.tenant_id, m.entity, new)
    ev = await r.audit_append(m.tenant_id, "WORKFLOW_TRANSITION", m.user_id, {"entity": m.entity, "transition": m.transition, "approval_id": str(m.approval_id)})
    return {"entity": m.entity, "new_status": new, "audit_seq": ev["seq"]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
