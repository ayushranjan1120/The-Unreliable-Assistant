# Audit Claims Register — `AUDIT-2026-11-13.md`

**Status of the source report:** it describes a *finished, hardened* build. **No such build exists in this repo** (no prototype, no code, no run artifacts). So every number in it is a **claim and a target, not evidence**. Nothing here may be reported as passed until our own run produces an artifact (design §1.15 Evidence Standard).

**How each claim is handled** (column "Mode"):
- **D** = *Designed-in* — already required by `design.md`; we adhere by building to it, then confirm in the version report.
- **B** = *Build-check* — checked in code review / CI during the build (cannot be seen in load numbers).
- **T** = *Test-verify* — only provable by running tests/load; verified at the iteration shown.

Iterations: **I1 = v0**, **I2 = v0.1**, **I3 = v0.2** (design §1.11.1). All statuses start **UNVERIFIED**.

## A. Claims → design mapping

| ID | Claim in report | Report value | Our gate (design) | Mode | Verify at | Status |
|---|---|---|---|---|---|---|
| C-01 | E2E latency p50/p95/p99 @ 50K | 185 / 390 / 540 ms | ≤ 240 / 450 / 590 (KPI-31..33) | T | I3 (L5), trend from I1–I2 | UNVERIFIED |
| C-02 | Throughput @ 50K, 5xx | 9,240 RPS, 0.01% | ≥ 8,500 RPS, ≤ 0.05% (KPI-34/35) | T | I3 (L5) | UNVERIFIED |
| C-03 | RAGAS faith / precision / recall | 0.94 / 0.91 / 0.89 | ≥ 0.92 / 0.88 / 0.85 (KPI-20, 11, §1.5) | T | I1 (L0), re-run every iteration under load | UNVERIFIED |
| C-04 | Token reduction | "52.4%" (table: 2,850→1,320) | ≥ 45% (KPI-26) | T | I1 (L0) | UNVERIFIED — see D-02 |
| C-05 | Cost / 1K queries | $0.38 | ≤ $0.45 (KPI-29) | T | I2 (L3) | UNVERIFIED |
| C-06 | Loop escape 100% | 100% | KPI-16 = 100% | T | I1 (L1 chaos) | UNVERIFIED |
| C-07 | HITL enforcement 100% | 100% | KPI-19 = 100% | B+T | I1 (L1) | UNVERIFIED |
| C-08 | Adversarial/PII block | 99.9% | KPI-03 ≥ 99.5%, KPI-02 ≥ 99.8% | T | I1 (L1) | UNVERIFIED — see D-03 |
| C-09 | "All 36 KPIs PASSED" | no per-KPI values | every KPI needs a number + artifact | T | every iteration | UNVERIFIED — see D-04 |
| C-10 | Zero cross-tenant cache leaks at 50K | 0 | KPI-28 = 0 leaks | B+T | I1 (L1 probe), I3 (50K) | UNVERIFIED |
| C-11 | Zero connection-pool exhaustion | 0 | bulkheads rag 64 / db 128 / llm 256; no `POOL_EXHAUSTED` events | T | I2–I3 | UNVERIFIED |
| C-12 | 100% mitigation indirect injection / schema fuzz / PII smuggling | 100% | KPI-03; red-team suite ≥ 2,000 executions | T | I1 | UNVERIFIED — see D-03 |
| C-13 | p95 contained in 450 ms per OTel traces | yes | span budgets §1.4 | T | I2–I3 | UNVERIFIED |
| C-14 | Triage temp 0.0, < 25 ms | yes | KPI-05 ≤ 25 ms p95 (30 ms hard timeout) | D+T | I1 (L0) | UNVERIFIED |
| C-15 | BM25 k1 1.25, b 0.75; chunk 512/64; RRF k 60; rerank > 0.42, FP16 ONNX | yes | §1.8 / ADR-1/2 | D+B | I1 | UNVERIFIED (config check) |
| C-16 | MCP servers `hybrid-rag`, `state-store`, `graphify-vault` + lifecycle plugins | yes | §1.2, §1.8 | D+B | I1 | UNVERIFIED |
| C-17 | Pydantic v2 auto-repair, GoalAnchorBlock, idempotent breaker | yes | §1.2.1, §1.8, ADR-4 | D+B | I1 | UNVERIFIED |
| C-18 | Redis key `SHA256(tenant_id:user_id:query)` tenant-isolated | yes | ADR-3 / ADR-6 (uses *normalised* query; adds L2) | D+B | I1 | UNVERIFIED — see D-07 |
| C-19 | Async workers, ONNX dynamic micro-batching | yes | §1.3, §1.4 R2 (batch 32 / 8 ms) | D+T | I2 | UNVERIFIED |
| C-20 | Baseline: 2,400/4,800/7,100 ms; crash at 120 VUs; 38.6% hallucination; 0.61/0.54/0.68; 42% PII leak | cited | none (we cannot re-measure) | — | — | NOT REPRODUCIBLE — see D-10 |

## B. Discrepancies, gaps and ambiguities found in the report (intake triage)

Classification per design §1.12: *Confirmed defect · Spec conflict · Design gap · Not reproducible · Won't fix*.

| ID | Finding | Class | Impact on our aim | Disposition (what we do) |
|---|---|---|---|---|
| D-01 | Says "3-Stage" and "sequential 4-stage" for the same pipeline. Counting fusion as a stage gives 4. "Sequential" BM25 → dense would add ~12 ms (37 ms vs the 25 ms parallel budget). | Spec conflict | Medium (latency) | Keep design: BM25 ‖ dense in parallel → RRF → rerank (3 retrieval stages + fusion step). Sequential is **not accepted** unless §1.4 budget still holds; record in VR. |
| D-02 | Token reduction stated as 52.4% but table gives 2,850 → 1,320 = **53.7%**. Cost falls 88.8% ($3.40 → $0.38), far more than tokens fell; unexplained (model change? cache hits? output tokens?). | Confirmed defect (internal inconsistency) | Medium (KPI-26, KPI-29 credibility) | Define measurement now: KPI-26 = (prompt tokens baseline − prompt tokens hardened) / baseline on the **same 250 golden queries**, input tokens only; KPI-29 = full cost incl. output tokens, cache hits counted separately. Report both and attribute the cost drop (model size per ADR-7, tokens, cache). |
| D-03 | "99.9% blocked" from a **100-prompt** log is not representable (resolution is 1%); §4 elsewhere claims **100%**. Also "zero breaches" is an absolute claim. | Confirmed defect | High (security gate) | Red-team = 100 seed prompts × mutations ≥ **2,000 executions**. Report *k misses in N trials* plus 95% upper bound (rule of three: ≈ 3/N). KPI-03 ≥ 99.5% requires N ≥ 600 with 0 misses to be statistically meaningful. "0 observed" is stated that way — never "100% proven". |
| D-04 | "All 36 KPIs PASSED" with values for only about 14. Missing: KPI-01, 04–10, 12–15, 17–18, 21–25, 27, 30, 36 (answer relevancy, entity recall, TTFT, MRR/nDCG, hit ratio, RSS soak …). | Design gap (evidence) | High | Version report must contain **all 36 values + run id + artifact path**. Blank = FAIL, not "assumed pass". |
| D-05 | 9,240 RPS at 50K VUs implies cycle ≈ 5.4 s (50,000 / 9,240) ⇒ ~5 s think time. With `sleep(1)` throughput would be ~40K RPS. Load profile is not disclosed. | Spec conflict (resolved) | Medium | Consistent with accepted **SC-08**. Report must state think time, ramp, injector count/CPU. |
| D-06 | Only a 50K summary is cited. No 5K/10K/25K results, no soak (KPI-36), no rollback drill, no chaos runs. | Design gap | High (our method) | Our ladder (L0–L6) supplies these; each version report lists every rung attempted and its result. |
| D-07 | Cache key uses raw `query`; design uses **normalised** query. KPI-27 (hit ratio ≥ 35%) not reported; a per-user key alone cannot reach it. | Spec conflict (resolved) | Medium | Follow ADR-3/ADR-6 (normalised key, L2 tenant tier). Report hit ratio split L1/L2 and shows 0 cross-tenant and 0 cross-user (non-shareable) leaks. |
| D-08 | Span names differ: report/KPI table use `triage`, `rag.bm25`, `rag.dense`, `rag.rerank`, `ragas`; Gate 10 uses `triage.classify`, `mcp.rag.bm25`, `mcp.rag.biencoder`, `mcp.rag.rerank`, `hook.post.ragas`. | Spec conflict | Low | Emit Gate-10 names; expose KPI aliases in the KPI evaluator (alias table in `utils/kpi_eval.py`). |
| D-09 | Roadmap lists LoRA fine-tuning, multi-region DB replication, TensorRT-LLM. | Won't fix (this cycle) | None | Out of scope for v0–v0.2 (design §1.1 non-goals note). TRT-LLM remains an allowed serving option (§1.4.1). Single-region + read replica only. |
| D-10 | No prototype or baseline artifacts exist here, so "before" numbers can't be re-measured. | Not reproducible | Low (before/after story only) | Keep baseline numbers as *cited, unmeasured*. If the original prototype is provided, add a baseline run at L0/L2; otherwise our reports show **absolute results vs gates**, not before/after deltas. |
| D-11 | Report numbers are results from a different system. Using them as gates would silently tighten the SLA. | Design gap | Medium | **Gates stay the design thresholds.** Report values are *reference expectations*: worse than the report but within the gate = PASS with a note; outside the gate = FAIL. |

## C. Standing rules this report creates
1. A claim is **never** marked PASS from this document. PASS requires our artifact (design §1.15).
2. Every version report (VR) updates the Status column above and attaches evidence paths.
3. Any claim still UNVERIFIED at the end of I3 is listed as an open gap in the final report — not omitted.
4. New contradictions found during build are appended to Section B with the next D-id.

## Status after VR-v0 (2026-11-18, dev-cpu, nothing certified)
| Claim | Report value | Our measured (v0) | Status |
|---|---|---|---|
| C-01 latency p50/p95/p99 @50K | 185/390/540 ms | not run (L5 requires target infra) | UNVERIFIED |
| C-02 throughput/5xx @50K | 9,240 RPS / 0.01% | not run | UNVERIFIED |
| C-03 RAGAS faith/prec/recall | 0.94/0.91/0.89 | proxies only: 1.00 (extractive, trivial) / ctx-precision 0.907 / entity-recall 0.969; relevancy proxy 0.805 | UNVERIFIED (generation + real RAGAS not run) |
| C-04 token reduction | 52.4% (table: 53.7%) | 68.3% on golden-250 (our definition, input tokens) | MEASURED PASS on our definition; cost attribution pending |
| C-05 cost/1K | $0.38 | $0.062 (assumed small-model pricing, extractive) | UNVERIFIED (assumption-based) |
| C-06 loop escape 100% | 100% | 8/8 breaker escapes; harness bench 20/20 unrepairable rejected | MEASURED PASS (n small) |
| C-07 HITL 100% | 100% | 0 executions without approval in 11 checks | MEASURED PASS (n small) |
| C-08/C-12 adversarial & PII block | 99.9% / 100% | injection HOLDOUT 66.9% (FAIL); PII 1,500/1,500 | CONTRADICTED for injection; PII PASS |
| C-09 "all 36 KPIs passed" | — | 15 pass · 6 fail · 6 informational · 9 no data | CONTRADICTED |
| C-10 zero cross-tenant leaks | 0 | 0 in 121 probes | MEASURED PASS at small scale; 50K not run |
| C-11 zero pool exhaustion | 0 | not run | UNVERIFIED |
| C-13 p95 within 450 ms per OTel | yes | spans emitted; budgets not met on CPU (hw-bound) | UNVERIFIED |
| C-14..C-19 config/design adherence | — | `scripts/audit_check.py` 28/28 | MEASURED PASS (build-time) |
| C-18 cache key | SHA256(tenant:user:query) | normalised key + L1/L2 implemented; L2 semantic cosine NOT implemented (D-16) | PARTIAL |
| C-20 baseline numbers | — | no prototype supplied | NOT REPRODUCIBLE |

New contradictions/deviations found while building: D-12 … D-21 (see `python -m scripts.audit_check` output).
