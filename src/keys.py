"""The ONLY module allowed to build Redis keys (design ADR-3, CLAUDE.md rule 1).

Every key is ``t:{tenant}:{ns}:...`` where ``{tenant}`` is a Redis hash-tag so one tenant maps to one slot.
Raw string keys elsewhere are lint-banned (tests/test_lint_rules.py).
"""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata

from src.errors import Code, ToolError

_TENANT = re.compile(r"^tenant_[A-Za-z0-9_-]{1,64}$")
_WS = re.compile(r"\s+")


def _tenant(tenant_id: str) -> str:
    if not _TENANT.match(tenant_id or ""):
        raise ToolError(Code.TENANT_VIOLATION, "invalid tenant_id")
    return f"t:{{{tenant_id}}}"


def norm_query(q: str) -> str:
    """Normalise a query for cache keys: NFKC, lowercase, collapse whitespace, strip trailing punctuation."""
    q = unicodedata.normalize("NFKC", q).lower()
    return _WS.sub(" ", q).strip().rstrip("?.! ")


def l1_key(tenant_id: str, user_id: str, query: str) -> str:
    """Per-user exact cache key: ``SHA256(tenant_id:user_id:norm_query)`` under the tenant prefix."""
    digest = hashlib.sha256(f"{tenant_id}:{user_id}:{norm_query(query)}".encode()).hexdigest()
    return f"{_tenant(tenant_id)}:l1:{digest}"


def l2_key(tenant_id: str, policy_version: str, query: str) -> str:
    """Tenant-scoped shareable cache key (exact-normalised tier of L2)."""
    digest = hashlib.sha256(f"{tenant_id}:{policy_version}:{norm_query(query)}".encode()).hexdigest()
    return f"{_tenant(tenant_id)}:l2:{digest}"


def l2_index(tenant_id: str, policy_version: str) -> str:
    """Hash holding L2 semantic entries for a tenant+policy version."""
    return f"{_tenant(tenant_id)}:l2idx:{policy_version}"


def lock_key(tenant_id: str, base_key: str) -> str:
    """Single-flight lock key for a cache key."""
    return f"{_tenant(tenant_id)}:lock:{hashlib.sha256(base_key.encode()).hexdigest()[:24]}"


def idem_key(tenant_id: str, idem_id: str) -> str:
    """Idempotency record key."""
    return f"{_tenant(tenant_id)}:idem:{idem_id}"


def breaker_key(tenant_id: str, tool: str, args_hash: str) -> str:
    """Circuit-breaker failure counter key."""
    return f"{_tenant(tenant_id)}:brk:{tool}:{args_hash}"


def breaker_pattern(tenant_id: str | None = None) -> str:
    """SCAN pattern for breaker keys (admin reset); ``None`` = all tenants."""
    return f"{_tenant(tenant_id)}:brk:*" if tenant_id else "t:*:brk:*"


def hot_key(tenant_id: str, session_id: str) -> str:
    """Hot ReAct memory (last 3 turns) list key."""
    return f"{_tenant(tenant_id)}:hot:{session_id}"


def ttl(base: int = 300, jitter: int = 45) -> int:
    """Jittered TTL (300 s ± 45 s) to prevent synchronized expiry / stampedes."""
    return base + random.randint(-jitter, jitter)


def tenant_of_key(key: str) -> str | None:
    """Extract the tenant id from a key (used by the leak probe and on-read assertion)."""
    m = re.match(r"^t:\{(tenant_[A-Za-z0-9_-]{1,64})\}:", key)
    return m.group(1) if m else None
