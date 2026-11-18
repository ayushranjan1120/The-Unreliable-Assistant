#!/usr/bin/env python3
"""Parse a k6 --out json stream + --summary-export into artifacts/load_<rung>.json and (optionally) KPI-31..35 records.
Usage: parse_k6.py <rung> <points.json> <summary.json> <vus_target> <think_s> <scale> <profile>"""
import json, random, sys, time
from pathlib import Path
from utils import results_store

rung, pts, summ, vus, think, scale, profile = sys.argv[1:8]
scale = float(scale); durs = []; fails = codes5xx = total = 0
for line in open(pts):
    try: d = json.loads(line)
    except Exception: continue
    if d.get("type") != "Point": continue
    m, v = d["metric"], d["data"]["value"]
    if m == "http_req_duration": durs.append(v)
    elif m == "http_reqs": total += 1
    elif m == "http_req_failed": fails += v
    elif m == "http_req_receiving" or m == "dropped_iterations": pass
S = json.load(open(summ))["metrics"]
dur = S["http_req_duration"]; reqs = S["http_reqs"]
res = {"run_id": results_store.new_run_id(f"load{rung}"), "rung": rung, "vus": int(float(vus)), "vus_spec": int(float(vus) * scale), "scale_divisor": scale, "think_s": float(think), "profile": profile,
       "p50": dur["med"], "p95": dur["p(95)"], "p99": dur["p(99)"], "max": dur["max"], "rps": reqs["rate"], "total_requests": reqs["count"],
       "fail_rate_pct": 100 * S["http_req_failed"]["value"], "certifiable": (profile == "target" and scale == 1)}
random.Random(5).shuffle(durs); res["samples_ms"] = durs[:5000]
Path("artifacts").mkdir(exist_ok=True)
Path(f"artifacts/load_{rung}.json").write_text(json.dumps(res)); Path("artifacts/load_latest.json").write_text(json.dumps(res))
if "--record" in sys.argv:
    art = f"artifacts/load_{rung}.json"
    rec = lambda k, v: results_store.record_run(k, v, n=res["total_requests"], run_id=res["run_id"], version=sys.argv[sys.argv.index("--record") + 1], rung=rung, profile=profile if res["certifiable"] else "dev-cpu", artifact=art,
                                                extra=None if res["certifiable"] else {"note": f"scaled-down 1/{scale:g}; NOT certifiable"})
    rec(31, res["p50"]); rec(32, res["p95"]); rec(33, res["p99"]); rec(34, res["fail_rate_pct"]); rec(35, res["rps"])
print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in res.items() if k != "samples_ms"}))
