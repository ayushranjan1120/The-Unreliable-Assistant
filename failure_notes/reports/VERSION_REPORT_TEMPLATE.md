# VR-<version> — Version Report (iteration <1|2|3>)

| Field | Value |
|---|---|
| Version / git tag | v0 / v0.1 / v0.2 |
| Date range | |
| Design version | D1.0 (lock sha256 ...) |
| Config hashes | mcp_config.json · index manifest (BM25/FAISS/graph) · LLM id + quantization + checksum · reranker threshold |
| Hardware | GPU type/count · CPU · injectors (count, CPU %) |
| Rungs attempted | L0 / L1 / L2 / L3 / L4 / L5 / L6 — result each |
| Highest rung passed | |
| Rollbacks performed | drills + real (time taken) |

## 1. Rung log
| Rung | VUs / think time / RPS offered | Duration | Injector CPU | Result | Run id |
|---|---|---|---|---|---|

## 2. All 36 KPIs (blank value = FAIL)
| KPI | Threshold | Measured | N | Run id | Artifact path | Pass/Fail | Audit-report reference value |
|---|---|---|---|---|---|---|---|
| 01 | ≤ 15 ms | | | | | | |
| … (all 36 rows) | | | | | | | |

## 3. RAGAS (overall + per stratum)
| Stratum | Faithfulness | Answer relevancy | Ctx precision | Ctx recall | Ctx entity recall |
|---|---|---|---|---|---|
| Factoid / Complex / Multi-hop / Summary / Negative / **Overall** | | | | | |

## 4. Latency & load
p50/p95/p99, RPS, 5xx, TTFT, per-span p95 vs §1.4 budget, top-20 slow traces, degraded-flag counts.

## 5. Security & isolation
Red-team: N executions, k misses, 95% upper bound · PII recall · cross-tenant probes (N, leaks) · Tier-3 without approval (count).

## 6. Failure notes
| FN id | Severity | KPI(s) | Status (open / fixed-in) |
|---|---|---|---|

## 7. Claims register delta (audit/CLAIMS_REGISTER.md)
| Claim | Previous status | New status | Evidence |
|---|---|---|---|

## 8. Fix list for next iteration
Ordered by impact on p95/p99 latency and accuracy at 50K.

## 9. Open gaps / owner decisions needed
