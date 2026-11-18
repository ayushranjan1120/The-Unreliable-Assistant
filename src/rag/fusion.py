"""Reciprocal Rank Fusion with dedup by chunk_hash and scale-normalized scores (design ADR-1)."""
from __future__ import annotations


def rrf(rankings: list[list[int]], hashes: list[str], k: int = 60, pool: int = 40, weights: list[float] | None = None) -> list[tuple[int, float, int]]:
    """Fuse rankings of doc indices. Returns ``[(doc_idx, normalized_score, rank)]`` (≤ pool).

    Docs sharing a chunk_hash are merged (best rank kept). Scores are scaled to (0, 1] by the max possible
    score so that they are comparable across queries.
    """
    w = weights or [1.0] * len(rankings)
    score: dict[int, float] = {}
    for wi, r in zip(w, rankings):
        for rank, d in enumerate(r, 1):
            score[d] = score.get(d, 0.0) + wi / (k + rank)
    best: dict[str, int] = {}
    for d in sorted(score, key=lambda x: -score[x]):          # dedup by chunk_hash, keep highest-scoring copy
        best.setdefault(hashes[d], d)
    kept = sorted(best.values(), key=lambda d: (-score[d], d))[:pool]
    top = sum(w) / (k + 1)
    return [(d, score[d] / top, i + 1) for i, d in enumerate(kept)]
