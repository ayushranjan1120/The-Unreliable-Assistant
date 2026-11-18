#!/usr/bin/env python3
"""mcp-hybrid-rag (stdio): the 3-stage retrieval stack served over MCP (design Gate 3 / §1.2.2).

Engine lives in ``src/rag`` (testable without MCP); this module is the server: tool ``hybrid_retrieve``,
lab-only ``hybrid_retrieve_debug``, resource ``rag://chunks/{id}``. Models load once per process at startup.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src import telemetry  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.contracts.models import HybridRetrieveIn  # noqa: E402
from src.rag.pipeline import HybridRetriever  # noqa: E402

S = get_settings()
mcp = FastMCP("hybrid-rag")
_R: HybridRetriever | None = None


def retriever() -> HybridRetriever:
    """Lazy singleton (warmed on first use)."""
    global _R
    if _R is None:
        _R = HybridRetriever(S)
        _R.warmup()
    return _R


async def _run(req: dict, explain: bool) -> dict:
    t = time.perf_counter()
    m = HybridRetrieveIn.model_validate_json(json.dumps(req))
    telemetry.start_trace(m.trace_id)
    out = await retriever().retrieve(m, explain=explain)
    out["server_ms"] = (time.perf_counter() - t) * 1000
    return out


@mcp.tool()
async def hybrid_retrieve(req: dict) -> dict:
    """BM25 ‖ bi-encoder → RRF → cross-encoder. Contract: HybridRetrieveIn → HybridRetrieveOut (abstain when top < threshold)."""
    return await _run(req, False)


@mcp.tool()
async def hybrid_retrieve_debug(req: dict) -> dict:
    """Lab-only: same as hybrid_retrieve plus per-candidate stage scores (BM25 / cosine / RRF / cross-encoder)."""
    return await _run(req, True)


@mcp.resource("rag://chunks/{chunk_id}")
def read_chunk(chunk_id: str) -> str:
    """Return one chunk (tenant filter applies at retrieval; chunk text is shared enterprise content in v0)."""
    r = retriever(); i = r.by_id.get(chunk_id)
    if i is None:
        raise ValueError("unknown chunk")
    c = r.chunks[i]
    return json.dumps({k: c[k] for k in ("chunk_id", "source_id", "domain", "risk_tier", "text")})


@mcp.resource("contract://hybrid_retrieve")
def contract() -> str:
    """JSON schema of the tool contract."""
    return json.dumps(HybridRetrieveIn.model_json_schema())


if __name__ == "__main__":
    retriever()                      # eager load + warmup BEFORE accepting requests (no model load inside a request budget)
    mcp.run(transport="stdio")
