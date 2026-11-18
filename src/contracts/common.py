"""Shared Pydantic v2 strict types (design §1.2.1)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

UUID4_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class Strict(BaseModel):
    """Base for every contract: strict types, no extras, immutable."""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


TenantId = Annotated[str, StringConstraints(pattern=r"^tenant_[A-Za-z0-9_-]{1,64}$")]
UserId = Annotated[str, StringConstraints(pattern=r"^user_[A-Za-z0-9_-]{1,64}$")]
SessionId = Annotated[str, StringConstraints(pattern=UUID4_RE)]
Uuid4Str = Annotated[str, StringConstraints(pattern=UUID4_RE)]
Sku = Annotated[str, StringConstraints(pattern=r"^SKU-\d{3}$")]
Lot = Annotated[str, StringConstraints(pattern=r"^LOT-\d{5}$")]
ChunkId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Query = Annotated[str, StringConstraints(min_length=1, max_length=2000)]

Tier = Literal["TIER_1_READ", "TIER_2_COMPUTE", "TIER_3_MUTATE"]
Route = Literal["DIRECT_ANSWER", "HYBRID_RAG", "TRANSACTION_EXEC", "REJECT_OOS"]
Status = Literal["OK", "PENDING_APPROVAL", "HALTED_BREAKER", "HALTED_BUDGET", "HALTED_NOOP", "REJECTED"]
Role = Literal["reader", "analyst", "operator", "approver", "auditor"]
TIER_RANK = {"TIER_1_READ": 1, "TIER_2_COMPUTE": 2, "TIER_3_MUTATE": 3}
ROLE_MAX_TIER = {"reader": 1, "analyst": 2, "operator": 3, "approver": 3, "auditor": 1}
