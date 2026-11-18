#!/usr/bin/env python3
"""Golden-250 end-to-end evaluation through the LIVE API (design Gate 3/8). Records KPI-17/18/20..24/26/29 evidence.

Metrics are RAGAS-COMPATIBLE PROXIES (no LLM judge on this host — audit register D-15); runs for KPI-20/21/22 are tagged
method=proxy so the scorecard shows them INFORMATIONAL (not certifiable) until the real RAGAS library + judge run on `target`.
  KPI-20 faithfulness (proxy)   mean NLI-supported claim ratio of answered items (same verifier as the veto: optimistic)
  KPI-21 answer relevancy (proxy) answer-bearing fact recall (golden entities not in the question) averaged over positives
  KPI-22 context entity recall   |golden entities ∩ (question ∪ retrieved context)| / |entities|   (RAGAS definition, our entity lists)
  KPI-23 abstention accuracy     negatives that abstained / were rejected (real)
  KPI-24 hallucination rate      answered items with ≥1 number/id not in the retrieved context (independent of the NLI veto)
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

from src.agent.verify import ungrounded_facts
from utils import results_store
from utils.token_calc import reduction_pct

ROOT = Path(__file__).resolve().parent.parent
NUM = re.compile(r"\d+")


def facts_present(g: dict, answer: str) -> tuple[int, int]:
    """(found, total) of answer-bearing golden facts (entities not already in the question)."""
    q = g["question"].lower()
    need = [e for e in g["entities"] if e.lower() not in q]
    if g["stratum"] == "complex" and "longer" in q:                       # comparison: answer must name the winner id
        need = [e for e in g["entities"] if e.lower() not in q] or g["entities"][:1]
    a = re.sub(r"\s+", " ", answer.lower())
    found = 0
    for e in need:
        el = e.lower()
        found += (el in a) or (el.split()[0] in a if NUM.match(el) else False)
    return found, len(need)


def main() -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000"); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--version", default="v0"); ap.add_argument("--profile", default="dev-cpu"); ap.add_argument("--tag", default="")
    ap.add_argument("--no-record", action="store_true"); ap.add_argument("--force-negatives", action="store_true", help="send negatives through generation (tests the model's own abstention)")
    ap.add_argument("--save", default="", help="write rows+summary to this JSON path without recording KPIs")
    a = ap.parse_args()
    G = [json.loads(l) for l in (ROOT / "eval/data/golden_250.jsonl").read_text().splitlines()]
    if a.limit:
        G = G[::max(1, len(G) // a.limit)][:a.limit]
    c = httpx.Client(timeout=120)
    rows, t_start = [], time.time()
    for i, g in enumerate(G):
        tenant, user = "tenant_eval", f"user_{i % 7}"
        r = c.post(f"{a.url}/api/v1/admin/query_debug", json={"tenant_id": tenant, "user_id": user, "query": g["question"], "role": "operator", **({"force_generate": True} if a.force_negatives and g["stratum"] == "negative" else {})}, headers={"X-Tenant-ID": tenant})
        d = r.json()
        rows.append({"g": g, "d": d})
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(G)} ({time.time() - t_start:.0f}s)", flush=True)
    per = defaultdict(lambda: defaultdict(list)); overall = defaultdict(list)
    ans_items, neg_ok, neg_n, halluc, answered = 0, 0, 0, 0, 0
    steps, lat = [], []
    for x in rows:
        g, d = x["g"], x["d"]
        ab = bool(d.get("abstained")) or d.get("route") == "REJECT_OOS"
        lat.append(d.get("latency_ms") or 0)
        steps.append(len([s for s in d.get("react_steps", []) if s["state"] in ("OBSERVATION", "TOOL_CALL")]) + 1)
        if g["stratum"] == "negative":
            neg_n += 1; neg_ok += ab
            continue
        ctx = " ".join(d.get("context") or [])
        ents = g["entities"]
        found = sum((e.lower() in ctx.lower()) or (e.lower() in g["question"].lower()) for e in ents)
        per[g["stratum"]]["entity_recall"].append(found / max(len(ents), 1))
        f, n = facts_present(g, d.get("answer") or "")
        per[g["stratum"]]["relevancy"].append(0.0 if ab else (f / n if n else 1.0))
        per[g["stratum"]]["answered"].append(0.0 if ab else 1.0)
        if not ab:
            answered += 1
            per[g["stratum"]]["faith"].append((d.get("verify") or {}).get("supported_ratio", d.get("faithfulness") or 0.0) if d.get("verify") else (d.get("faithfulness") or 0.0))
            if ungrounded_facts(d.get("answer") or "", ctx):
                halluc += 1
    m = lambda k: st.mean(v for s in per.values() for v in s[k]) if any(per[s][k] for s in per) else 0.0
    res = {"n": len(G), "n_pos": len(G) - neg_n, "n_neg": neg_n, "faithfulness_proxy": m("faith"), "answer_relevancy_proxy": m("relevancy"), "context_entity_recall": m("entity_recall"),
           "answered_rate_pos": m("answered"), "abstention_accuracy_pct": 100 * neg_ok / max(neg_n, 1), "hallucination_rate_pct": 100 * halluc / max(answered, 1), "answered": answered,
           "mean_steps": st.mean(steps), "latency_ms": {"p50": sorted(lat)[len(lat) // 2], "p95": sorted(lat)[int(len(lat) * .95)]},
           "per_stratum": {k: {mk: (st.mean(mv) if mv else None) for mk, mv in v.items()} for k, v in per.items()}}
    from collections import Counter
    res["llm_backends"] = dict(Counter((x["d"].get("llm") or {}).get("backend", "none") for x in rows))
    res["veto_first_pct"] = 100 * sum("vetoed_first=True" in " ".join(s["observation"] for s in x["d"].get("react_steps", [])) for x in rows) / max(sum(bool(x["d"].get("llm")) for x in rows), 1)
    tt = sorted((x["d"].get("llm") or {}).get("ttft_ms", 0) for x in rows if x["d"].get("llm"))
    res["ttft_ms"] = {"n": len(tt), "p50": tt[len(tt) // 2] if tt else None, "p95": tt[int(len(tt) * .95)] if tt else None}
    res["neg_answered"] = sum(1 for x in rows if x["g"]["stratum"] == "negative" and not (bool(x["d"].get("abstained")) or x["d"].get("route") == "REJECT_OOS"))
    if a.save:
        Path(a.save).write_text(json.dumps({**res, "rows": [{"id": x["g"]["id"], "stratum": x["g"]["stratum"], "q": x["g"]["question"], "gt": x["g"]["ground_truth"], "draft": x["d"].get("draft"), "answer": x["d"].get("answer"),
                                                              "abstained": x["d"].get("abstained"), "llm": x["d"].get("llm"), "verify": x["d"].get("verify")} for x in rows]}, indent=1))
    st_ = c.get(f"{a.url}/api/v1/admin/status", timeout=30).json()["runtime"]
    if st_["completed"]:
        res["token_reduction_pct"] = reduction_pct(st_["tokens_baseline"], st_["tokens_prompt"]); res["cost_per_1k"] = 1000 * st_["cost"] / st_["completed"]
    if not a.no_record:
        rid = results_store.new_run_id("golden"); art = ROOT / "artifacts" / f"golden_eval_{rid}{a.tag}.json"
        art.write_text(json.dumps({**res, "rows": [{"id": x["g"]["id"], "q": x["g"]["question"], "gt": x["g"]["ground_truth"], "answer": x["d"].get("answer"), "abstained": x["d"].get("abstained"), "route": x["d"].get("route"),
                                                    "degraded": x["d"].get("degraded")} for x in rows]}, indent=1))
        rec = lambda k, v, n, **ex: results_store.record_run(k, v, n=n, run_id=rid, version=a.version, rung="L0", profile=a.profile, artifact=str(art.relative_to(ROOT)), extra=ex or None)
        rec(20, res["faithfulness_proxy"], answered, method="proxy"); rec(21, res["answer_relevancy_proxy"], res["n_pos"], method="proxy"); rec(22, res["context_entity_recall"], res["n_pos"], method="proxy-entity-lists")
        rec(23, res["abstention_accuracy_pct"], neg_n); rec(24, res["hallucination_rate_pct"], answered); rec(17, res["mean_steps"], len(G))
        if "token_reduction_pct" in res:
            rec(26, res["token_reduction_pct"], st_["completed"]); rec(29, res["cost_per_1k"], st_["completed"])
        res["run_id"] = rid
    return res


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
