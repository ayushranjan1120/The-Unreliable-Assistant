"""Triage agent (Gate 6): deterministic (temperature-0-equivalent) classifier → (intent, domain, risk_tier, complexity) + route.

v0 is rules-first (microseconds, no model) rather than a distilled neural classifier — audit register D-14. If accuracy
(KPI-04) fails, an embedding/distilled model is the v0.1 candidate. Never routes to TRANSACTION_EXEC on timeout/uncertainty.
"""
from __future__ import annotations

import re

from src import telemetry
from src.agent.grounding import OOV_RATIO_GATE, coverage_ratio, entity_ids
from src.contracts.models import TriageOut

_MUTATE = re.compile(r"\b(release|quarantine|recall|approve|reject|hold|ship|destroy|rework|unquarantine|set status|move)\b", re.I)
_LOT = re.compile(r"\blot[-_ ]?\d+\b", re.I)
_STATUS = re.compile(r"\b(status|state|where is|is .* released)\b", re.I)
_DIRECT = re.compile(r"^\s*(hi( there)?|hello( there)?|hey( there)?|good (morning|afternoon|evening)|thanks?( you)?[ ,.!a-z]*|thank you|bye|goodbye|help|who are you\??|what can you do\??|can you help me( with [a-z ]+)?\??)\s*[.!?]*\s*$", re.I)
_DOMAINS = (("safety", r"recall|hazard|safety"), ("quality", r"quarantin|release|inspect|qa|nonconform"), ("logistics", r"zone|storage|temperature|excursion"),
            ("procurement", r"supplier|lead time|audit"), ("compliance", r"retain|retention|record"), ("catalog", r"sku|shelf|order|product"))
_COMPLEX = re.compile(r"\b(compare|which has|longer|shorter|difference|and by how|how many .* and|within how many)\b", re.I)


def classify(query: str, vocab: frozenset[str] | None = None) -> TriageOut:
    """Classify one (already redacted) query."""
    with telemetry.span("triage.classify"):
        q = query.strip()
        ents = entity_ids(q)
        has_lot = bool(_LOT.search(q))
        domain = next((d for d, rx in _DOMAINS if re.search(rx, q, re.I)), "general")
        if _DIRECT.match(q):
            return TriageOut(intent="chitchat", domain="general", risk_tier="TIER_1_READ", complexity="low", route="DIRECT_ANSWER", confidence=0.99)
        if has_lot and _MUTATE.search(q):
            return TriageOut(intent="mutate", domain=domain, risk_tier="TIER_3_MUTATE", complexity="low", route="TRANSACTION_EXEC", confidence=0.95)
        if has_lot and _STATUS.search(q) and not re.search(r"protocol|procedure|policy|steps", q, re.I):
            return TriageOut(intent="lookup", domain=domain, risk_tier="TIER_1_READ", complexity="low", route="TRANSACTION_EXEC", confidence=0.9)
        if not ents and vocab is not None and coverage_ratio(q, vocab) >= OOV_RATIO_GATE:
            return TriageOut(intent="out_of_scope", domain="general", risk_tier="TIER_1_READ", complexity="low", route="REJECT_OOS", confidence=0.9)
        cx = "high" if len(ents) >= 2 or _COMPLEX.search(q) else ("medium" if len(q.split()) > 12 else "low")
        return TriageOut(intent="question", domain=domain, risk_tier="TIER_1_READ", complexity=cx, route="HYBRID_RAG", confidence=0.85)
