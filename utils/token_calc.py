"""Token accounting proxy. Pure Python.

A single counter is used for every KPI-26/29 computation so before/after numbers are comparable.
The proxy (word + punctuation pieces, scaled) approximates BPE tokenizers; it is NOT a model tokenizer.
"""
from __future__ import annotations

import re

_PIECE = re.compile(r"\w+|[^\w\s]")
SCALE = 1.3  # words→BPE tokens factor for English prose


def count(text: str) -> int:
    """Approximate token count of ``text``."""
    return int(round(len(_PIECE.findall(text)) * SCALE))


def truncate(text: str, max_tokens: int) -> str:
    """Truncate to ≤ ``max_tokens`` at a piece boundary, preserving the original characters."""
    limit = int(max_tokens / SCALE)
    n = 0
    for m in _PIECE.finditer(text):
        n += 1
        if n > limit:
            return text[:m.start()].rstrip()
    return text


def reduction_pct(baseline_tokens: float, new_tokens: float) -> float:
    """KPI-26: (baseline − new) / baseline × 100 on input tokens."""
    return 0.0 if baseline_tokens <= 0 else 100.0 * (baseline_tokens - new_tokens) / baseline_tokens
