"""Tool and API contracts (design §1.2.2). All models are strict, extra-forbid, frozen."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from src.contracts.common import (ChunkId, Lot, Query, Route, SessionId, Sku, Status, Strict, TenantId,
                                  Tier, TraceId, UserId)


class HybridRetrieveIn(Strict):
    tenant_id: TenantId
    query: Query
    top_k: Annotated[int, Field(ge=5, le=50)] = 50
    rrf_k: Annotated[int, Field(ge=1, le=200)] = 60
    pool: Annotated[int, Field(ge=4, le=100)] = 40
    rerank_k: Annotated[int, Field(ge=1, le=10)] = 4
    threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.42
    k1: Annotated[float, Field(ge=0.1, le=5.0)] = 1.25
    b: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75
    idempotency_key: UUID
    trace_id: TraceId


class Chunk(Strict):
    chunk_id: ChunkId
    text: str
    source_id: str
    domain: str
    risk_tier: Tier
    bm25: float | None = None
    cosine: float | None = None
    rrf_rank: int | None = None
    ce_score: Annotated[float, Field(ge=0, le=1)]


class HybridRetrieveOut(Strict):
    chunks: list[Chunk]
    top_score: float
    abstain: bool
    stage_ms: dict[Literal["bm25", "dense", "rrf", "rerank"], float]
    degraded: bool = False


class SubgraphIn(Strict):
    tenant_id: TenantId
    anchor: str
    depth: Annotated[int, Field(ge=0, le=2)] = 2
    max_tokens: Annotated[int, Field(ge=64, le=512)] = 320
    idempotency_key: UUID
    trace_id: TraceId


class SubgraphOut(Strict):
    nodes: list[dict]
    edges: list[tuple[str, str]]
    text: str
    token_count: int
    raw_token_estimate: int


class FactsPutIn(Strict):
    tenant_id: TenantId
    session_id: SessionId
    fact: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_chunk_id: ChunkId
    idempotency_key: UUID
    trace_id: TraceId


class FactsGetIn(Strict):
    tenant_id: TenantId
    session_id: SessionId
    idempotency_key: UUID
    trace_id: TraceId


class WorkflowTransitionIn(Strict):
    """Tier-3 mutation: requires an approved HITL record (approval_id)."""
    tenant_id: TenantId
    user_id: UserId
    entity: Annotated[str, Field(pattern=r"^(SKU-\d{3}|LOT-\d{5})$")]
    transition: Literal["RELEASE", "QUARANTINE", "RECALL", "APPROVE_BATCH"]
    approval_id: UUID | None = None
    idempotency_key: UUID
    trace_id: TraceId


class LotStatusIn(Strict):
    """Tier-1 read: look up a lot's status."""
    tenant_id: TenantId
    lot: Lot
    idempotency_key: UUID
    trace_id: TraceId


class SkuLookupIn(Strict):
    tenant_id: TenantId
    sku: Sku
    idempotency_key: UUID
    trace_id: TraceId


class AgentQueryIn(Strict):
    tenant_id: TenantId
    user_id: UserId
    query: Query
    session_id: SessionId | None = None
    role: Literal["reader", "analyst", "operator", "approver", "auditor"] = "operator"


class AgentQueryOut(Strict):
    tenant_id: TenantId
    route: Route
    answer: str | None
    citations: list[ChunkId]
    abstained: bool
    status: Status
    faithfulness: float | None = None
    degraded: bool = False
    trace_id: TraceId
    latency_ms: float | None = None
    approval_id: str | None = None
    remediation: str | None = None


class TriageOut(Strict):
    intent: str
    domain: str
    risk_tier: Tier
    complexity: Literal["low", "medium", "high"]
    route: Route
    confidence: float


class AuditAppendIn(Strict):
    """TIER_2_COMPUTE: append a hash-chained audit event (payload must already be PII-redacted)."""
    tenant_id: TenantId
    event_type: Annotated[str, Field(pattern=r"^[A-Z_]{3,40}$")]
    actor: Annotated[str, Field(min_length=1, max_length=80)]
    payload: dict
    idempotency_key: UUID
    trace_id: TraceId
