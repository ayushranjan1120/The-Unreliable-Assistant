"""3-stage hybrid retrieval engine (design Gate 3): BM25 ‖ bi-encoder+FAISS → RRF → cross-encoder → abstention."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np

from src import telemetry
from src.config import Settings
from src.contracts.models import HybridRetrieveIn
from src.rag.batcher import MicroBatcher
from src.rag.bm25 import BM25Index, tokenize
from src.rag.fusion import rrf
from src.rag.index import MODELS_DIR, load_index
from src.rag.onnx_models import OnnxCrossEncoder, OnnxEncoder

RERANK_TIMEOUT_MS = 120


class HybridRetriever:
    """Loads the read-only index + ONNX models once per process and serves retrievals."""

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.chunks, self.embs, self.faiss, self.manifest = load_index(s.index_dir)
        self.hashes = [c["chunk_hash"] for c in self.chunks]
        self.bm25 = BM25Index([c["tokens"] for c in self.chunks])
        self.enc = OnnxEncoder(MODELS_DIR / "bi", 512)
        self.ce = OnnxCrossEncoder(MODELS_DIR / "rerank", 512)
        self.enc_batcher = MicroBatcher(lambda qs: list(self.enc.encode(qs, is_query=True)), 32, 8.0)
        self.ce_batcher = MicroBatcher(lambda pairs: list(self.ce.score(pairs)), 32, 8.0)
        self.by_id = {c["chunk_id"]: i for i, c in enumerate(self.chunks)}

    def warmup(self) -> None:
        """Run one dummy retrieval worth of inference so first-request latency isn't measured as steady state."""
        self.enc.encode(["warmup"], is_query=True)
        self.ce.score([("warmup", "warmup text")])

    def allowed(self, i: int, tenant: str) -> bool:
        """Tenant filter: shared content or the caller's own tenant only."""
        return self.chunks[i]["tenant"] in ("shared", tenant)

    async def _bm25(self, req: HybridRetrieveIn) -> list[tuple[int, float]]:
        with telemetry.span("mcp.rag.bm25"):
            toks = tokenize(req.query)
            res = await asyncio.to_thread(self.bm25.search, toks, req.k1, req.b, req.top_k * 2)
            return [r for r in res if self.allowed(r[0], req.tenant_id)][:req.top_k]

    async def _dense(self, req: HybridRetrieveIn) -> list[tuple[int, float]]:
        with telemetry.span("mcp.rag.biencoder"):
            (qv,) = await self.enc_batcher.submit([req.query])
            self.faiss.hnsw.efSearch = self.s.efsearch
            D, I = await asyncio.to_thread(self.faiss.search, np.asarray([qv], dtype=np.float32), req.top_k)
            return [(int(i), float(d)) for i, d in zip(I[0], D[0]) if i >= 0 and self.allowed(int(i), req.tenant_id)]

    async def retrieve(self, req: HybridRetrieveIn, explain: bool = False) -> dict[str, Any]:
        """Return a HybridRetrieveOut-shaped dict (plus ``candidates`` when ``explain``)."""
        sparse, dense = await asyncio.gather(self._bm25(req), self._dense(req))
        with telemetry.span("mcp.rag.rrf") as sp_rrf:
            fused = rrf([[d for d, _ in sparse], [d for d, _ in dense]], self.hashes, req.rrf_k, req.pool)
        bm_score, bm_rank = dict(sparse), {d: r for r, (d, _) in enumerate(sparse, 1)}
        cos, d_rank = dict(dense), {d: r for r, (d, _) in enumerate(dense, 1)}
        degraded = False
        pool_ids = [d for d, _, _ in fused]
        with telemetry.span("mcp.rag.rerank") as sp_rr:
            try:
                scores = await asyncio.wait_for(self.ce_batcher.submit([(req.query, self.chunks[d]["text"]) for d in pool_ids]),
                                                RERANK_TIMEOUT_MS * self.s.time_scale / 1000) if pool_ids else []
                ce = {d: float(sc) for d, sc in zip(pool_ids, scores)}
            except Exception:                                     # FMEA #2: reranker timeout/failure → RRF fallback, stricter abstention
                degraded = True
                telemetry.METRICS.inc("rag.rerank.fallback")
                ce = {d: (sc if (d in bm_rank and d in d_rank) else 0.0) for d, sc, _ in fused}   # both retrievers must agree
        ranked = sorted(pool_ids, key=lambda d: -ce.get(d, 0.0))
        top_score = ce[ranked[0]] if ranked else 0.0
        abstain = top_score < req.threshold
        keep = [] if abstain else [d for d in ranked[:req.rerank_k] if ce[d] >= req.threshold]
        out_chunks = [{"chunk_id": self.chunks[d]["chunk_id"], "text": self.chunks[d]["text"], "source_id": self.chunks[d]["source_id"],
                       "domain": self.chunks[d]["domain"], "risk_tier": self.chunks[d]["risk_tier"], "bm25": bm_score.get(d),
                       "cosine": cos.get(d), "rrf_rank": next((r for dd, _, r in fused if dd == d), None), "ce_score": min(1.0, max(0.0, ce[d]))}
                      for d in keep]
        ms = {n: next((s["dur_ms"] for s in (telemetry.current().spans if telemetry.current() else []) if s["name"] == f"mcp.rag.{n}"), 0.0)
              for n in ("bm25", "biencoder", "rrf", "rerank")}
        res: dict[str, Any] = {"chunks": out_chunks, "top_score": top_score, "abstain": abstain, "degraded": degraded,
                               "stage_ms": {"bm25": ms["bm25"], "dense": ms["biencoder"], "rrf": sp_rrf["dur_ms"], "rerank": sp_rr["dur_ms"]}}
        if explain:
            res["candidates"] = [{"rank": i + 1, "chunk_id": self.chunks[d]["chunk_id"], "source": self.chunks[d]["source_id"],
                                  "bm25": round(bm_score.get(d, 0.0), 3), "bm25_rank": bm_rank.get(d), "cosine": round(cos.get(d, 0.0), 4),
                                  "dense_rank": d_rank.get(d), "rrf_rank": next((r for dd, _, r in fused if dd == d), None),
                                  "rrf_score": round(next((sc for dd, sc, _ in fused if dd == d), 0.0), 4), "ce_score": round(ce.get(d, 0.0), 4)}
                                 for i, d in enumerate(ranked[:20])]
            res["recall_pool"] = sorted({self.chunks[d]["source_id"] for d, _ in sparse} | {self.chunks[d]["source_id"] for d, _ in dense})
        return res
