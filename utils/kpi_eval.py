"""Canonical 36-KPI specification and evidence-standard evaluator (design §1.5, §1.15).

Pure Python: no Streamlit imports. A KPI passes only with a measured value *and* evidence
(run id + artifact path). Missing data is NO_DATA, never PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Op = Literal["<=", ">=", "=="]
Status = Literal["PASS", "FAIL", "NO_DATA", "NO_EVIDENCE", "INFORMATIONAL"]

# Rung ordering used to decide which KPIs block a given rung.
RUNGS = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]


@dataclass(frozen=True)
class KpiSpec:
    """One row of the design §1.5 matrix."""
    id: int
    stage: str
    name: str
    op: Op
    threshold: float
    unit: str
    rung: str
    tool: str
    hw_bound: bool = False  # GPU/scale-bound: not certifiable under profile 'dev-cpu' (design §1.16)

    @property
    def key(self) -> str:
        return f"KPI-{self.id:02d}"


def _k(i, stage, name, op, thr, unit, rung, tool, hw=False) -> KpiSpec:
    return KpiSpec(i, stage, name, op, thr, unit, rung, tool, hw)


KPI_SPECS: list[KpiSpec] = [
    _k(1, "1 Ingress & Triage", "Pre-hook guardrail latency p95", "<=", 15, "ms", "L0", "OTel hook.pre"),
    _k(2, "1 Ingress & Triage", "PII redaction recall", ">=", 99.8, "%", "L0", "Presidio/regex audit"),
    _k(3, "1 Ingress & Triage", "Prompt-injection block rate", ">=", 99.5, "%", "L1", "Red-team benchmark"),
    _k(4, "1 Ingress & Triage", "Triage routing accuracy", ">=", 97.0, "%", "L0", "Labeled intent set"),
    _k(5, "1 Ingress & Triage", "Triage latency p95", "<=", 25, "ms", "L0", "OTel triage.classify"),
    _k(6, "1 Ingress & Triage", "False-positive refusal on benign", "<=", 1.0, "%", "L0", "Benign control set"),
    _k(7, "2 Hybrid Retrieval", "BM25 latency p95", "<=", 12, "ms", "L0", "OTel mcp.rag.bm25"),
    _k(8, "2 Hybrid Retrieval", "Bi-encoder + FAISS latency p95", "<=", 25, "ms", "L0", "OTel mcp.rag.biencoder", True),
    _k(9, "2 Hybrid Retrieval", "Cross-encoder rerank latency p95", "<=", 45, "ms", "L0", "OTel mcp.rag.rerank", True),
    _k(10, "2 Hybrid Retrieval", "Hybrid context recall@50", ">=", 0.92, "", "L0", "RAGAS / IR eval"),
    _k(11, "2 Hybrid Retrieval", "Post-rerank context precision@4", ">=", 0.88, "", "L0", "RAGAS"),
    _k(12, "2 Hybrid Retrieval", "MRR@4", ">=", 0.85, "", "L0", "IR benchmark"),
    _k(13, "2 Hybrid Retrieval", "nDCG@4", ">=", 0.86, "", "L0", "IR benchmark"),
    _k(14, "3 Agent & Loop", "Tool-schema first-pass validation", ">=", 96.0, "%", "L1", "Pydantic telemetry"),
    _k(15, "3 Agent & Loop", "Auto-repair success on malformed args", ">=", 90.0, "%", "L1", "Harness metrics"),
    _k(16, "3 Agent & Loop", "Infinite-loop escape rate", "==", 100.0, "%", "L1", "Chaos loop suite"),
    _k(17, "3 Agent & Loop", "Mean ReAct steps per task", "<=", 2.2, "steps", "L1", "OTel trace count"),
    _k(18, "3 Agent & Loop", "Context drift (goal cosine)", ">=", 0.85, "", "L1", "Embedding drift audit"),
    _k(19, "3 Agent & Loop", "Tier-3 HITL enforcement", "==", 100.0, "%", "L1", "Audit-log integrity"),
    _k(20, "4 Generation & RAGAS", "RAGAS faithfulness", ">=", 0.92, "", "L0", "Post-hook RAGAS"),
    _k(21, "4 Generation & RAGAS", "RAGAS answer relevancy", ">=", 0.90, "", "L0", "Post-hook RAGAS"),
    _k(22, "4 Generation & RAGAS", "RAGAS context entity recall", ">=", 0.85, "", "L0", "Post-hook RAGAS"),
    _k(23, "4 Generation & RAGAS", "Unanswerable abstention accuracy", ">=", 98.0, "%", "L0", "Negative test set"),
    _k(24, "4 Generation & RAGAS", "Hallucination rate", "<=", 0.5, "%", "L0", "NLI claim verifier"),
    _k(25, "4 Generation & RAGAS", "TTFT p95", "<=", 180, "ms", "L2", "FastAPI stream metric", True),
    _k(26, "5 Token & Graphify", "Prompt-token reduction", ">=", 45.0, "%", "L0", "Token counter hook"),
    _k(27, "5 Token & Graphify", "Semantic cache hit ratio", ">=", 35.0, "%", "L3", "Redis hit/miss"),
    _k(28, "5 Token & Graphify", "Cross-tenant cache isolation (leaks)", "==", 0, "leaks", "L1", "Concurrency probe"),
    _k(29, "5 Token & Graphify", "Cost per 1,000 queries", "<=", 0.45, "$", "L3", "OTel token-cost calc", True),
    _k(30, "5 Token & Graphify", "MCP JSON-RPC overhead p95", "<=", 4, "ms", "L0", "MCP client timer"),
    _k(31, "6 Scale & Load (50K)", "E2E latency p50 @50K", "<=", 240, "ms", "L5", "k6 + JMeter", True),
    _k(32, "6 Scale & Load (50K)", "E2E latency p95 @50K", "<=", 450, "ms", "L5", "k6 + JMeter", True),
    _k(33, "6 Scale & Load (50K)", "E2E latency p99 @50K", "<=", 590, "ms", "L5", "k6 + JMeter", True),
    _k(34, "6 Scale & Load (50K)", "HTTP 5xx rate @50K", "<=", 0.05, "%", "L5", "k6 / JMeter", True),
    _k(35, "6 Scale & Load (50K)", "Sustained throughput", ">=", 8500, "RPS", "L5", "k6 / JMeter", True),
    _k(36, "6 Scale & Load (50K)", "RSS growth over 1-hr soak", "<=", 3.0, "%", "L6", "Prometheus node exporter", True),
]
SPEC_BY_ID: dict[int, KpiSpec] = {s.id: s for s in KPI_SPECS}

# KPI-table span aliases -> Gate-10 span names (audit D-08).
SPAN_ALIASES = {
    "triage": "triage.classify", "rag.bm25": "mcp.rag.bm25", "rag.dense": "mcp.rag.biencoder",
    "rag.rerank": "mcp.rag.rerank", "ragas": "hook.post.ragas",
}


def compare(op: Op, value: float, threshold: float) -> bool:
    """Return True if ``value`` satisfies ``op threshold``."""
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    return abs(value - threshold) < 1e-9


def evaluate_kpi(spec: KpiSpec, run: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate one KPI against a run record under the evidence standard (§1.15).

    ``run`` keys: value, n, run_id, version, rung, profile, artifact. Returns a row dict.
    """
    row = {"kpi": spec.key, "stage": spec.stage, "name": spec.name, "target": f"{spec.op} {spec.threshold}{spec.unit}",
           "rung": spec.rung, "value": None, "n": None, "run_id": None, "profile": None, "artifact": None,
           "status": "NO_DATA"}
    if not run or run.get("value") is None:
        return row
    row.update({k: run.get(k) for k in ("value", "n", "run_id", "profile", "artifact")})
    if not run.get("run_id") or not run.get("artifact"):
        row["status"] = "NO_EVIDENCE"
        return row
    if str(run.get("method", "")).startswith("proxy"):        # RAGAS-compatible proxy, no LLM judge: informational until real RAGAS runs
        row["status"] = "INFORMATIONAL"
        row["passes_threshold"] = compare(spec.op, float(run["value"]), spec.threshold)
        return row
    if spec.hw_bound and run.get("profile") != "target":
        row["status"] = "INFORMATIONAL"  # design §1.16: not certifiable under dev-cpu
        row["passes_threshold"] = compare(spec.op, float(run["value"]), spec.threshold)
        return row
    row["status"] = "PASS" if compare(spec.op, float(run["value"]), spec.threshold) else "FAIL"
    return row


def scorecard(latest_runs: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate all 36 KPIs from ``{kpi_id: run_record}``."""
    return [evaluate_kpi(s, latest_runs.get(s.id)) for s in KPI_SPECS]


def summarize(rows: list[dict[str, Any]], up_to_rung: str | None = None) -> dict[str, int | bool]:
    """Count statuses; ``certified`` is True only if every in-scope KPI is PASS."""
    scope = [r for r in rows if up_to_rung is None or RUNGS.index(r["rung"]) <= RUNGS.index(up_to_rung)]
    counts = {s: sum(r["status"] == s for r in scope) for s in
              ("PASS", "FAIL", "NO_DATA", "NO_EVIDENCE", "INFORMATIONAL")}
    counts["total"] = len(scope)
    counts["certified"] = bool(scope) and counts["PASS"] == len(scope)
    return counts
