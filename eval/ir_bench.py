#!/usr/bin/env python3
"""L0 IR benchmark (KPI-07..13, retrieval half of KPI-23): runs the real retriever over golden-250.

Metrics (binary relevance = chunk's note id in the question's gt_notes; negatives excluded except for abstention):
  recall@50  – gt notes covered by BM25∪dense candidate union (KPI-10, micro-averaged over gt notes)
  ctx_precision@4 – RAGAS-style average precision over the top-4 reranked chunks (KPI-11)
  MRR@4, nDCG@4 – over the top-4 reranked chunks (KPI-12/13)
  abstention – negatives with top_score < threshold / positives with top_score >= threshold
Writes artifacts/ir_bench_<run>.json and appends KPI records. ``--sweep`` prints a threshold sweep (does not change config).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from pathlib import Path

from src import telemetry
from src.config import get_settings
from src.contracts.models import HybridRetrieveIn
from src.agent.grounding import gate_reason, rewrite_query
from src.graphify import GraphIndex
from src.rag.pipeline import HybridRetriever
from utils import results_store

ROOT = Path(__file__).resolve().parent.parent


def ctx_precision_at_k(rel: list[int]) -> float:
    """RAGAS context precision: mean of precision@i over relevant positions."""
    hits, acc = 0, 0.0
    for i, r in enumerate(rel, 1):
        if r:
            hits += 1; acc += hits / i
    return acc / hits if hits else 0.0


def ndcg(rel: list[int], n_rel_total: int) -> float:
    """nDCG@len(rel) with binary relevance."""
    dcg = sum(r / math.log2(i + 1) for i, r in enumerate(rel, 1))
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(n_rel_total, len(rel)) + 1))
    return dcg / ideal if ideal else 0.0


async def run(args: argparse.Namespace) -> dict:
    s = get_settings()
    R = HybridRetriever(s); R.warmup()
    GI = GraphIndex(s.vault_dir, s.graph_path)
    G = [json.loads(l) for l in (ROOT / "eval/data/golden_250.jsonl").read_text().splitlines()]
    thr = args.threshold if args.threshold is not None else s.manifest.rerank_threshold
    rows = []
    for g in G:
        telemetry.start_trace()
        rq, _ = rewrite_query(g["question"], GI) if not args.raw else (g["question"], [])
        req = HybridRetrieveIn.model_validate_json(json.dumps({"tenant_id": "tenant_1", "query": rq, "threshold": 0.0,
                                                               "idempotency_key": str(uuid.uuid4()), "trace_id": "a" * 32}))
        r = await R.retrieve(req, explain=True)
        gate = gate_reason(g["question"], GI) is not None
        rows.append({"id": g["id"], "stratum": g["stratum"], "gt": g["gt_notes"], "cands": r["candidates"], "pool": r["recall_pool"], "gate": gate,
                     "degraded": r["degraded"], "stage_ms": r["stage_ms"]})
    pos = [x for x in rows if x["stratum"] != "negative"]
    neg = [x for x in rows if x["stratum"] == "negative"]
    rec_num = rec_den = 0
    prec, mrr, nd = [], [], []
    per = {}
    for x in pos:
        gt = set(x["gt"]); rec_num += len(gt & set(x["pool"])); rec_den += len(gt)
        top4 = [c["source"] in gt for c in x["cands"][:4]]
        rel = [int(v) for v in top4]
        p, m, n = ctx_precision_at_k(rel), next((1 / i for i, r in enumerate(rel, 1) if r), 0.0), ndcg(rel, len(gt))
        prec.append(p); mrr.append(m); nd.append(n)
        d = per.setdefault(x["stratum"], {"p": [], "m": [], "n": []}); d["p"].append(p); d["m"].append(m); d["n"].append(n)
    avg = lambda v: sum(v) / len(v) if v else 0.0
    top = lambda x: (x["cands"][0]["ce_score"] if x["cands"] else 0.0) * (0.0 if (x["gate"] and not args.no_gate) else 1.0)
    sweep = []
    for t in [i / 100 for i in range(5, 100, 5)]:
        tn = sum(top(x) < t for x in neg); tp = sum(top(x) >= t for x in pos)
        sweep.append({"threshold": t, "neg_abstain_pct": 100 * tn / len(neg), "pos_answer_pct": 100 * tp / len(pos), "balanced": 50 * (tn / len(neg) + tp / len(pos))})
    pct = lambda name, p: telemetry.METRICS.percentile(name, p)
    for x in rows:
        for k, v in x["stage_ms"].items():
            telemetry.METRICS.observe(f"stage.{k}", v)
    res = {"n_pos": len(pos), "n_neg": len(neg), "threshold": thr, "recall_at_50": 100 * rec_num / rec_den / 100, "ctx_precision_at_4": avg(prec),
           "mrr_at_4": avg(mrr), "ndcg_at_4": avg(nd), "per_stratum": {k: {"ctx_precision": avg(v["p"]), "mrr": avg(v["m"]), "ndcg": avg(v["n"]), "n": len(v["p"])} for k, v in per.items()},
           "abstain_neg_pct": 100 * sum(top(x) < thr for x in neg) / len(neg), "answer_pos_pct": 100 * sum(top(x) >= thr for x in pos) / len(pos),
           "degraded_count": sum(x["degraded"] for x in rows), "sweep": sweep,
           "latency_p95_ms": {k: pct(f"stage.{k}", 95) for k in ("bm25", "dense", "rrf", "rerank")}, "time_scale": s.time_scale}
    if not args.no_record:
        rid = results_store.new_run_id("ir"); art = ROOT / "artifacts" / f"ir_bench_{rid}.json"
        art.write_text(json.dumps(res, indent=1))
        rec = lambda k, v, n: results_store.record_run(k, v, n=n, run_id=rid, version=s.version, rung="L0", profile=s.profile, artifact=str(art.relative_to(ROOT)))
        rec(7, res["latency_p95_ms"]["bm25"], len(rows)); rec(8, res["latency_p95_ms"]["dense"], len(rows)); rec(9, res["latency_p95_ms"]["rerank"], len(rows))
        rec(10, res["recall_at_50"], rec_den); rec(11, res["ctx_precision_at_4"], len(pos)); rec(12, res["mrr_at_4"], len(pos)); rec(13, res["ndcg_at_4"], len(pos))
        res["run_id"] = rid
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--threshold", type=float); ap.add_argument("--no-record", action="store_true"); ap.add_argument("--sweep", action="store_true"); ap.add_argument("--raw", action="store_true"); ap.add_argument("--no-gate", action="store_true")
    r = asyncio.run(run(ap.parse_args()))
    sweep = r.pop("sweep")
    print(json.dumps(r, indent=1))
    print("threshold sweep (neg abstain% / pos answer% / balanced):")
    for x in sweep:
        print(f"  {x['threshold']:.2f}  {x['neg_abstain_pct']:5.1f}  {x['pos_answer_pct']:5.1f}  {x['balanced']:5.1f}")
