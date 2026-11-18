"""Observation compression: strip boilerplate JSON keys, extractive, ≤ 180 tokens (design §1.8, Gate 5.2)."""
from __future__ import annotations

import re
from typing import Any

from utils.token_calc import count, truncate

BOILERPLATE = {"jsonrpc", "id", "meta", "_meta", "trace_id", "idempotency_key", "stage_ms", "degraded", "version", "schema",
               "tenant_id", "ts", "created_at", "request_id", "bm25", "cosine", "rrf_rank", "chunk_id", "ce_score", "domain", "risk_tier", "top_score", "abstain", "server_ms", "ms", "degraded", "source", "kind", "dist"}
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-z0-9\-]{3,}")


def flatten(obj: Any, prefix: str = "") -> list[str]:
    """Flatten JSON into ``key: value`` lines, dropping boilerplate keys."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BOILERPLATE:
                continue
            if isinstance(v, (dict, list)):
                out += flatten(v, f"{prefix}{k}.")
            else:
                out.append(str(v) if k == "text" else f"{prefix}{k}: {v}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += flatten(v, prefix)
    elif obj is not None:
        out.append(f"{prefix.rstrip('.')}: {obj}" if prefix else str(obj))
    return out


def compress(obj: Any, goal_terms: set[str] | None = None, max_tokens: int = 180) -> str:
    """Return an extractive text of ≤ ``max_tokens`` keeping the sentences most relevant to the goal."""
    text = obj if isinstance(obj, str) else "\n".join(flatten(obj))
    if count(text) <= max_tokens:
        return text
    goal = {t.lower() for t in (goal_terms or set())}
    sents = [s.strip() for s in _SENT.split(text) if s.strip()]

    def score(s: str) -> float:
        words = {w.lower() for w in _WORD.findall(s)}
        ids = len(re.findall(r"\b(?:SKU-\d{3}|LOT-\d{5})\b", s))
        return 2.0 * len(words & goal) + 1.5 * ids + 0.2 * len(re.findall(r"\d", s)) / (1 + len(s) / 80)

    ranked = sorted(range(len(sents)), key=lambda i: (-score(sents[i]), i))
    keep, used = set(), 0
    for i in ranked:
        c = count(sents[i])
        if used + c <= max_tokens:
            keep.add(i)
            used += c
    out = " ".join(sents[i] for i in sorted(keep))
    return truncate(out or text, max_tokens)
