"""Stage 1 — Sparse lexical: BM25Okapi + PorterStemmer + inverted index, pre-tokenized (design Gate 3).

k1/b are applied at query time from stored term frequencies, so the Streamlit lab can change them without
re-indexing. Scores are verified equal to ``rank_bm25.BM25Okapi`` in tests.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from nltk.stem import PorterStemmer

_stem = PorterStemmer()
_PIECE = re.compile(r"[a-z0-9]+")
_ID = re.compile(r"\b(?:sku|lot)-\d+\b")
_STEM_CACHE: dict[str, str] = {}
STOP = frozenset("a an the of to in on for and or is are was were be by with as at from that this it its what which who how when where do does did".split())


def stem(w: str) -> str:
    """Cached Porter stem."""
    s = _STEM_CACHE.get(w)
    if s is None:
        s = _STEM_CACHE[w] = _stem.stem(w)
    return s


def tokenize(text: str) -> list[str]:
    """Lowercase, split, drop stopwords, stem; keep hyphenated SKU/LOT ids as extra exact tokens."""
    low = text.lower()
    toks = [stem(w) for w in _PIECE.findall(low) if w not in STOP]
    return toks + _ID.findall(low)


class BM25Index:
    """Inverted index: ``term -> {doc_idx: tf}`` plus document lengths."""

    def __init__(self, docs_tokens: list[list[str]]) -> None:
        self.n = len(docs_tokens)
        self.dl = [len(d) for d in docs_tokens]
        self.avgdl = sum(self.dl) / max(self.n, 1)
        self.post: dict[str, dict[int, int]] = {}
        for i, d in enumerate(docs_tokens):
            for t, c in Counter(d).items():
                self.post.setdefault(t, {})[i] = c
        # Okapi idf (rank_bm25 convention incl. epsilon floor)
        idf = {t: math.log((self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}
        self.eps = 0.25 * (sum(idf.values()) / max(len(idf), 1))
        self.idf = {t: (v if v >= 0 else self.eps) for t, v in idf.items()}

    def search(self, query_tokens: list[str], k1: float = 1.25, b: float = 0.75, top_k: int = 50) -> list[tuple[int, float]]:
        """Return ``[(doc_idx, score)]`` sorted desc; only documents in the postings of query terms are touched."""
        scores: dict[int, float] = {}
        for t in query_tokens:
            p = self.post.get(t)
            if not p:
                continue
            idf = self.idf[t]
            for d, tf in p.items():
                scores[d] = scores.get(d, 0.0) + idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.dl[d] / self.avgdl))
        return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]
