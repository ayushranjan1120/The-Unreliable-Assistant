#!/usr/bin/env python3
"""KPI-04 (routing accuracy on the labeled intent set) and KPI-05 (triage latency p95). The labeled set is synthetic/templated (see FN notes)."""
import json
import time
from pathlib import Path

from src.agent.grounding import vault_vocab
from src.agent.triage import classify
from src.config import get_settings
from src.graphify import GraphIndex
from utils import results_store

ROOT = Path(__file__).resolve().parent.parent
s = get_settings()
V = vault_vocab(GraphIndex(s.vault_dir, s.graph_path))
T = [json.loads(l) for l in (ROOT / "eval/data/triage_labeled.jsonl").read_text().splitlines()]
lat, ok, miss = [], 0, []
for x in T:
    t = time.perf_counter(); r = classify(x["query"], V).route; lat.append((time.perf_counter() - t) * 1000)
    ok += r == x["route"]
    if r != x["route"]:
        miss.append((x["route"], r, x["query"][:70]))
lat.sort(); p95 = lat[int(len(lat) * .95)]; acc = 100 * ok / len(T)
rid = results_store.new_run_id("triage"); art = ROOT / "artifacts" / f"triage_bench_{rid}.json"
art.write_text(json.dumps({"n": len(T), "accuracy_pct": acc, "p95_ms": p95, "misses": miss}, indent=1))
rec = lambda k, v: results_store.record_run(k, v, n=len(T), run_id=rid, version=s.version, rung="L0", profile=s.profile, artifact=str(art.relative_to(ROOT)))
rec(4, acc); rec(5, p95)
print(f"triage accuracy {acc:.2f}% (n={len(T)}) p95 {p95:.3f} ms; misses: {miss}")
