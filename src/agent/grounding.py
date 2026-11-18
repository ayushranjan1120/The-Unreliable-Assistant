"""Retrieval-subagent grounding helpers (pure Python, deterministic, no LLM):

* ``rewrite_query``  – Graphify-chain query expansion (design §1.8: "query rewriting")
* ``unknown_entities`` – entities in the query that do not exist in the catalog → deterministic abstention
* ``coverage_gap``   – vocabulary gate: content terms of the question that appear NOWHERE in the vault (e.g. price, color)
"""
from __future__ import annotations

import re

from src.graphify import GraphIndex
from src.harness.repair import normalize_entities
from src.rag.bm25 import STOP, stem

_WORD = re.compile(r"[a-z]{3,}")
try:                                              # standard English function words (closed class), not tuned to our data
    from nltk.corpus import stopwords as _sw
    _NLTK_STOP = frozenset(_sw.words("english"))
except Exception:                                 # corpus missing → fall back to the small built-in list
    _NLTK_STOP = frozenset()
# Closed-class comparison/quantity words used as question scaffolding.
COMPARATIVES = frozenset("longer shorter longest shortest higher lower highest lowest greater fewer more less earlier later bigger smaller larger".split())
_ID = re.compile(r"\b(?:sku|lot)[-_ ]?\d+\b", re.I)
# Words that carry no attribute meaning for coverage (question scaffolding, domain boilerplate).
GENERIC = frozenset("""what many much long often does give need needed required require requires must should would could will can
please tell show summarize summary overview steps step procedure units unit lot lots batch product products item
number count which whose then there their them been have has had having made make using use used per every each""".split())


def _terms(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(_ID.sub(" ", text.lower())) if w not in STOP and w not in GENERIC
            and w not in _NLTK_STOP and w not in COMPARATIVES}


def entity_ids(query: str) -> list[str]:
    """Canonical SKU/LOT ids in ``query``."""
    return normalize_entities(query)


def unknown_entities(query: str, g: GraphIndex) -> list[str]:
    """SKU ids mentioned in the query with no catalog note (lots are runtime state, not vault content)."""
    return [e for e in entity_ids(query) if e.startswith("SKU-") and e.lower() not in g.notes]


def rewrite_query(query: str, g: GraphIndex) -> tuple[str, list[str]]:
    """Expand ``query`` with the heads of the 2 chain notes (from each SKU anchor) most lexically related to it.

    Returns ``(rewritten_query, appended_note_ids)``; unchanged when no SKU anchor exists.
    """
    skus = [e for e in entity_ids(query) if e.startswith("SKU-") and e.lower() in g.notes]
    if len(skus) != 1:                                    # multi-anchor comparisons need the anchor notes themselves
        return query, []
    qt = _terms(query)
    sub = g.subgraph(skus[0], depth=2, max_tokens=512)
    scored = []
    for n in sub["nodes"]:
        nid = n["id"]
        if nid == sub["anchor"]:
            continue
        body = g.notes[nid].body
        scored.append((len(qt & _terms(body + " " + nid.replace("-", " "))), nid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = [nid for sc, nid in scored[:2] if sc >= 1]
    heads = []
    for nid in picked:
        b = g.notes[nid].body
        heads.append(b.split(":")[0] if ":" in b[:60] else b[:60])
    return (query + " " + " ".join(heads)).strip() if heads else query, picked


def vault_vocab(g: GraphIndex) -> frozenset[str]:
    """Stems of every content word in the vault (cached on the index object)."""
    v = getattr(g, "_vocab", None)
    if v is None:
        v = frozenset(t for n in g.notes.values() for t in _terms(n.body + " " + n.id.replace("-", " ")))
        g._vocab = v  # type: ignore[attr-defined]
    return v


def coverage_gap(query: str, vocab: frozenset[str]) -> list[str]:
    """Content stems of the query that the vault never mentions (empty list = answerable vocabulary)."""
    return sorted(_terms(query) - vocab)


OOV_RATIO_GATE = 0.65   # calibrated on golden negatives (lowest negative ratio 0.67); risk of set-specific tuning noted in FN-v0-002


def coverage_ratio(query: str, vocab: frozenset[str]) -> float:
    """Fraction of the question's content terms the vault never mentions."""
    t = _terms(query)
    return len(t - vocab) / len(t) if t else 0.0


def gate_reason(query: str, g: GraphIndex) -> str | None:
    """Deterministic pre-generation abstention reason, or None if the question is answerable in principle."""
    unk = unknown_entities(query, g)
    if unk:
        return f"unknown_entity:{','.join(unk)}"
    v = vault_vocab(g)
    if coverage_ratio(query, v) >= OOV_RATIO_GATE:
        return f"vocab_gap:{','.join(coverage_gap(query, v))[:60]}"
    return None
