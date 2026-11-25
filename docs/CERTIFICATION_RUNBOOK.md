# Certification runbook (v0.2 → 50K users)

**Status:** v0.2 is *release-ready*, **not certified**. The development host (4 vCPU, no GPU) cannot produce valid results above a smoke test, so L2–L6 are NOT RUN. This runbook turns certification into a repeatable exercise on target infrastructure. Nothing here is a claim of passing.

## 1. Target infrastructure (design §1.3, §1.16)
| Component | Requirement | Why |
|---|---|---|
| App tier | N stateless FastAPI workers behind Envoy/NGINX (`--timeout-keep-alive` ≥ 75 s on **both**, idle timeout > think time) | k6 think time 5 s; default 5 s keep-alive caused connection resets in v0 smoke |
| GPU host(s) | CUDA, onnxruntime-gpu; FP16 `bge-reranker-v2-m3` + `bge-large-en-v1.5`; micro-batch 32 / 8 ms | KPI-08/09 (≤25/≤45 ms) are GPU-bound |
| LLM | **Self-hosted** vLLM/TRT-LLM small model (ADR-7), continuous batching + prefix cache. Hosted free tiers (Groq 8K tokens/min) cannot sustain even L2 | KPI-25 TTFT ≤180 ms, KPI-29 cost |
| Postgres | **Co-located** with the app (same region/AZ), primary + read replica, pgvector; run `python -m scripts.neon_setup`-style role split (app role must be `NOBYPASSRLS`) | Neon from the dev VM = 427 ms RTT; a Tier-3 approval needs ~12 round trips |
| Redis | Cluster (hash-tag per tenant) | KPI-27/28 |
| Injectors | k6 distributed (operator or ≥N VMs) + JMeter 1 controller + 10 workers; **never** on the SUT host; CPU < 70% | SC-14 validity |
| Observability | OTel Collector → Prometheus + Tempo + Grafana, alerts from design §1.10 | KPI-31–36 |

## 2. Environment (`.env` / deployment)
`PROFILE=target TIME_SCALE=1 LLM_BACKEND=openai_compat LLM_URL=http://<vllm>:8000 LLM_MODEL=<ADR-7 winner> PG_DSN=<app role, co-located> REDIS_URL=<cluster> BUILD_VERSION=v0.2` and **do not set** `STATE_TIMEOUT_MS`. Export target models (`python -m scripts.setup_models` currently exports the dev-cpu set; for `target` export the `MANIFESTS['target']` models to ONNX FP16 on the GPU host, rebuild the index and **re-calibrate the 0.42 abstention threshold** for the new reranker — ADR-2).

## 3. Order of operations (never skip a rung)
1. Pre-flight: `python -m pytest -q`, `python -m scripts.audit_check` (28/28), `python -m eval.system_tests` (16/16), `python -m scripts.rollback_drill` (5/5) on the target stack.
2. ADR-7 bake-off for the self-hosted model with the **accuracy-parity gate** (design §1.4.1, per stratum) before freezing it.
3. `scripts/certify.sh L2` (5K) → `L3` (10K) → `L4` (25K, with FMEA chaos #1–#6) → `L5` (50K, **×3 runs** + JMeter) → L6 1-hour soak (KPI-36). Each rung: all KPIs ≤ that rung must pass; accuracy KPIs re-run under load.
4. Any abort condition (5xx >1%/60 s, p99 >1.5 s/60 s, any cross-tenant leak, Tier-3 without approval, injector CPU ≥85%) → stop, **roll back**, write `FN-v0.2-NNN`, fix, restart from the lowest affected rung.
5. `python -m scripts.make_vr v0.2`; every KPI needs value, N, run id, artifact; `profile=target`. Update `audit/CLAIMS_REGISTER.md`; tag only after all 36 pass.

## 4. What is still known-open going in (see VR-v0.2)
KPI-03 injection block rate (<99.5% on independent data), KPI-01 pre-hook latency on CPU, KPI-13 nDCG (dev reranker), real-RAGAS (needs a judge), and everything scale-bound. Owner decisions: GPU sizing, region, model after bake-off.
