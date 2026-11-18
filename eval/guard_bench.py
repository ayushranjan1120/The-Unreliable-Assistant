#!/usr/bin/env python3
"""L0/L1 guardrail benchmark → KPI-01, 02, 03, 06 (records evidence). KPI-03 uses the HOLDOUT (seeds are tuned-on)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from src import telemetry
from src.config import get_settings
from src.plugins.hooks import PluginCtx, PluginMeta, PluginRegistry
from src.plugins.pii import PiiRedactor
from eval import redteam as rt
from utils import results_store

ROOT = Path(__file__).resolve().parent.parent


def main() -> dict:
    s = get_settings(); rid = results_store.new_run_id("guard")
    shield = rt.make_shield()
    reg = PluginRegistry()
    reg.register(PiiRedactor(), PluginMeta("pii_redactor", "PRE_EXECUTION", 10, True))
    reg.register(shield, PluginMeta("injection_shield", "PRE_EXECUTION", 20, True))
    benign = [json.loads(l)["query"] for l in (ROOT / "eval/data/benign_control.jsonl").read_text().splitlines()]
    telemetry.METRICS.reset()
    for q in benign[:200]:                                  # KPI-01: hook.pre latency on benign traffic (pii + regex + classifier)
        try:
            reg.run_pre(PluginCtx("tenant_1", "user_1", str(uuid.uuid4()), "a" * 32), q)
        except Exception:
            pass
    p95 = telemetry.METRICS.percentile("hook.pre", 95)
    r = rt.run_redteam(); pb = rt.pii_bench(); fp = rt.fp_bench()
    h = r["holdout"]; hn = sum(v["n"] for v in h.values()); hb = sum(v["blocked"] for v in h.values())
    out = {"hook_pre_p95_ms": p95, "pii": pb, "redteam": {k: v for k, v in r.items() if k != "by_category"}, "by_category": {k: {"n": v["n"], "miss": v["miss"], "examples": v["examples"]} for k, v in r["by_category"].items()},
           "holdout_block_rate_pct": 100 * hb / hn, "holdout_n": hn, "fp": fp}
    art = ROOT / "artifacts" / f"guard_bench_{rid}.json"; art.write_text(json.dumps(out, indent=1))
    rec = lambda k, v, n: results_store.record_run(k, v, n=n, run_id=rid, version=s.version, rung="L0", profile=s.profile, artifact=str(art.relative_to(ROOT)),
                                                  extra={"note": "KPI-03 value is HOLDOUT block rate; seed block rate is in the artifact (tuned-on, not evidence)"} if k == 3 else None)
    rec(1, p95, 200); rec(2, pb["recall_pct"], pb["n"]); rec(3, out["holdout_block_rate_pct"], hn); rec(6, fp["fp_rate_pct"], fp["n"])
    out["run_id"] = rid
    return out


if __name__ == "__main__":
    o = main()
    print(json.dumps({"hook_pre_p95_ms": o["hook_pre_p95_ms"], "pii_recall": o["pii"]["recall_pct"], "pii_n": o["pii"]["n"], "seed_block_rate": o["redteam"]["block_rate_pct"], "seed_n": o["redteam"]["block_executions"],
                      "holdout_block_rate": o["holdout_block_rate_pct"], "holdout_n": o["holdout_n"], "holdout": o["redteam"]["holdout"], "fp_rate": o["fp"]["fp_rate_pct"], "fp_n": o["fp"]["n"], "run_id": o["run_id"]}, indent=1))
