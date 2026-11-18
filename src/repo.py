"""The ONLY module that runs SQL (CLAUDE.md rule 1). Every function takes ``tenant_id`` and sets the RLS GUC.

Audit: per-tenant SHA-256 hash chain (``hash = SHA256(prev_hash + canonical_json(payload))``), append-only
(trigger), written under a per-tenant advisory lock. Payloads must already be PII-redacted.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from src.errors import Code, ToolError

GENESIS = "0" * 64


def canonical(payload: Any) -> str:
    """Deterministic JSON used for hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def chain_hash(prev_hash: str, payload: Any) -> str:
    """``SHA256(prev_hash + canonical_json(payload))``."""
    return hashlib.sha256((prev_hash + canonical(payload)).encode()).hexdigest()


class Repo:
    """Tenant-scoped data access over an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def create(cls, dsn: str, size: int = 128) -> "Repo":
        """Create the pool (``db_pool`` bulkhead)."""
        return cls(await asyncpg.create_pool(dsn, min_size=min(8, size), max_size=size, command_timeout=5))

    @asynccontextmanager
    async def tx(self, tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
        """Transaction with ``app.tenant_id`` set so row-level security applies."""
        if not tenant_id.startswith("tenant_"):
            raise ToolError(Code.TENANT_VIOLATION, "tenant_id required")
        async with self.pool.acquire() as c:
            async with c.transaction():
                await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
                yield c

    # ---- audit -------------------------------------------------------------------------------------------------
    async def audit_append(self, tenant_id: str, event_type: str, actor: str, payload: dict) -> dict:
        """Append one chained event; returns ``{seq, hash}``."""
        async with self.tx(tenant_id) as c:
            await c.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant_id)
            row = await c.fetchrow("SELECT seq, hash FROM audit_log WHERE tenant_id=$1 ORDER BY seq DESC LIMIT 1", tenant_id)
            seq, prev = (row["seq"] + 1, row["hash"]) if row else (1, GENESIS)
            h = chain_hash(prev, {"event": event_type, "actor": actor, "payload": payload, "seq": seq})
            await c.execute("INSERT INTO audit_log (tenant_id, seq, event_type, actor, payload, prev_hash, hash) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)",
                            tenant_id, seq, event_type, actor, canonical(payload), prev, h)
            return {"seq": seq, "hash": h}

    async def audit_verify(self, tenant_id: str) -> dict:
        """Recompute the chain; ``{ok, events, broken_at}``."""
        async with self.tx(tenant_id) as c:
            rows = await c.fetch("SELECT seq, event_type, actor, payload, prev_hash, hash FROM audit_log WHERE tenant_id=$1 ORDER BY seq", tenant_id)
        prev = GENESIS
        for r in rows:
            expect = chain_hash(prev, {"event": r["event_type"], "actor": r["actor"], "payload": json.loads(r["payload"]), "seq": r["seq"]})
            if r["prev_hash"] != prev or r["hash"] != expect:
                return {"ok": False, "events": len(rows), "broken_at": r["seq"]}
            prev = r["hash"]
        return {"ok": True, "events": len(rows), "broken_at": None}

    # ---- session facts -----------------------------------------------------------------------------------------
    async def facts_put(self, tenant_id: str, session_id: str, fact: str, evidence_chunk_id: str) -> None:
        async with self.tx(tenant_id) as c:
            await c.execute("INSERT INTO session_facts (tenant_id, session_id, fact, evidence_chunk_id, hash) VALUES ($1,$2::uuid,$3,$4,$5)",
                            tenant_id, session_id, fact, evidence_chunk_id, hashlib.sha256(fact.encode()).hexdigest()[:16])

    async def facts_get(self, tenant_id: str, session_id: str) -> list[dict]:
        async with self.tx(tenant_id) as c:
            rows = await c.fetch("SELECT fact, evidence_chunk_id, created_at FROM session_facts WHERE tenant_id=$1 AND session_id=$2::uuid ORDER BY id DESC LIMIT 50", tenant_id, session_id)
        return [{"fact": r["fact"], "evidence_chunk_id": r["evidence_chunk_id"], "created_at": r["created_at"].isoformat()} for r in rows]

    # ---- lots ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _default_lot_status(tenant_id: str, lot: str) -> str:
        return ("RELEASED", "HELD_FOR_QA", "QUARANTINED", "RELEASED", "RELEASED")[int(hashlib.sha256(f"{tenant_id}{lot}".encode()).hexdigest(), 16) % 5]

    async def lot_status(self, tenant_id: str, lot: str) -> dict:
        async with self.tx(tenant_id) as c:
            r = await c.fetchrow("SELECT status, updated_at FROM lot_state WHERE tenant_id=$1 AND lot=$2", tenant_id, lot)
        return {"lot": lot, "status": r["status"] if r else self._default_lot_status(tenant_id, lot), "source": "ledger" if r else "default"}

    async def lot_set(self, tenant_id: str, lot: str, status: str) -> None:
        async with self.tx(tenant_id) as c:
            await c.execute("INSERT INTO lot_state (tenant_id, lot, status) VALUES ($1,$2,$3) ON CONFLICT (tenant_id, lot) DO UPDATE SET status=$3, updated_at=now()", tenant_id, lot, status)

    # ---- HITL ----------------------------------------------------------------------------------------------------
    async def hitl_check_or_create(self, tenant_id: str, requester: str, tool: str, args_hash: str, args: dict) -> tuple[str, str]:
        """Return ``(approval_id, 'APPROVED')`` and consume it if an approved record exists; else ``(id, 'PENDING_APPROVAL')``."""
        async with self.tx(tenant_id) as c:
            r = await c.fetchrow("SELECT approval_id, status FROM hitl WHERE tenant_id=$1 AND requester=$2 AND tool=$3 AND args_hash=$4 AND status IN ('APPROVED','PENDING_APPROVAL') ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                                 tenant_id, requester, tool, args_hash)
            if r and r["status"] == "APPROVED":
                await c.execute("UPDATE hitl SET status='CONSUMED' WHERE tenant_id=$1 AND approval_id=$2", tenant_id, r["approval_id"])
                return str(r["approval_id"]), "APPROVED"
            if r:
                return str(r["approval_id"]), "PENDING_APPROVAL"
            aid = uuid.uuid4()
            await c.execute("INSERT INTO hitl (tenant_id, approval_id, requester, tool, args_hash, args, status) VALUES ($1,$2,$3,$4,$5,$6::jsonb,'PENDING_APPROVAL')",
                            tenant_id, aid, requester, tool, args_hash, canonical(args))
            return str(aid), "PENDING_APPROVAL"

    async def hitl_list(self, tenant_id: str) -> list[dict]:
        async with self.tx(tenant_id) as c:
            rows = await c.fetch("SELECT approval_id, requester, tool, args, created_at FROM hitl WHERE tenant_id=$1 AND status='PENDING_APPROVAL' ORDER BY created_at LIMIT 100", tenant_id)
        return [{"approval_id": str(r["approval_id"]), "requester": r["requester"], "action": f"{r['tool']} {json.loads(r['args']) if isinstance(r['args'], str) else r['args']}", "created_at": r["created_at"].isoformat()} for r in rows]

    async def hitl_decide(self, tenant_id: str, approval_id: str, approver: str, decision: str) -> dict:
        if decision not in ("APPROVED", "REJECTED"):
            raise ToolError(Code.VALIDATION_FAILED, "decision must be APPROVED or REJECTED")
        async with self.tx(tenant_id) as c:
            r = await c.fetchrow("SELECT requester, status FROM hitl WHERE tenant_id=$1 AND approval_id=$2::uuid FOR UPDATE", tenant_id, approval_id)
            if not r or r["status"] != "PENDING_APPROVAL":
                raise ToolError(Code.VALIDATION_FAILED, "no pending approval with that id")
            if r["requester"] == approver:
                raise ToolError(Code.RBAC_DENIED, "requester cannot approve their own request")
            await c.execute("UPDATE hitl SET status=$3, approver=$4, decided_at=now() WHERE tenant_id=$1 AND approval_id=$2::uuid", tenant_id, approval_id, decision, approver)
        return {"approval_id": approval_id, "status": decision}

    async def hitl_status(self, tenant_id: str, approval_id: str) -> str | None:
        async with self.tx(tenant_id) as c:
            r = await c.fetchrow("SELECT status FROM hitl WHERE tenant_id=$1 AND approval_id=$2::uuid", tenant_id, approval_id)
        return r["status"] if r else None
