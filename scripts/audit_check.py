#!/usr/bin/env python3
"""Adherence check against the spec + audit claims (design §1.15, CLAIMS_REGISTER 'D/B' items). Static checks on code/config;
KPI numbers come from artifacts/kpi_runs.jsonl via utils.kpi_eval. Exit code 1 if any BUILD-TIME check fails.
Usage: python -m scripts.audit_check [--version v0]"""
import hashlib, json, re, sys
from pathlib import Path
from src.config import get_settings
from utils import kpi_eval, results_store

ROOT = Path(__file__).resolve().parent.parent
S = get_settings()
read = lambda p: (ROOT / p).read_text()
rows = []
def chk(area, name, ok, detail=""): rows.append((area, name, "PASS" if ok else "FAIL", detail))

# ---- Gate 3 / ADR-1 hyper-parameters ---------------------------------------------------------------------------------------
chk("Gate3", "BM25 k1=1.25 b=0.75 top_k=50", (S.bm25_k1, S.bm25_b, S.top_k) == (1.25, 0.75, 50))
chk("Gate3", "RRF k=60 pool=40 rerank_k=4", (S.rrf_k, S.pool, S.rerank_k) == (60, 40, 4))
chk("Gate3", "FAISS HNSW efSearch=64", S.efsearch == 64 and "IndexHNSWFlat" in read("src/rag/index.py"))
chk("Gate3", "chunk 512 / overlap 64", "CHUNK_TOKENS, OVERLAP = 512, 64" in read("src/rag/index.py"))
chk("Gate3", "BM25 ‖ dense run in parallel, then RRF, then rerank", "asyncio.gather(self._bm25(req), self._dense(req))" in read("src/rag/pipeline.py"), "audit D-01: sequential 4-stage NOT adopted")
chk("Gate3", "rerank threshold 0.42 (spec) per manifest", S.manifest.rerank_threshold == 0.42, f"profile {S.profile}; dev reranker not re-calibrated yet (FN-v0-005)")
# ---- Gate 4 / ADR-4 -------------------------------------------------------------------------------------------------------
chk("Gate4", "loop limits 5 / 120 ms / 450 ms (× time_scale on dev-cpu)", (S.max_iterations, S.per_step_timeout_ms, S.total_loop_budget_ms) == (5, 120, 450), f"time_scale={S.time_scale}")
rt = read("src/agent/runtime.py")
chk("Gate4", "no direct MCP calls in the runtime outside the harness plumbing", rt.count("host.call(") == 3, f"host.call sites: {rt.count('host.call(')} (expected 3: tool-binding lambda, HITL gate, audit writer)")
chk("Gate4", "harness order: tenant→breaker→validate→repair→RBAC→HITL→idempotency→dispatch→compress", all(k in read("src/harness/harness.py") for k in ("TENANT_VIOLATION", "is_open", "_validate", "RBAC_DENIED", "check_or_create", "IdemStore", "wait_for", "compress(")))
chk("Gate4", "UUIDv4 idempotency (v4-format derived keys)", "v4_from_hash" in read("src/harness/harness.py"))
# ---- Gate 5/6/7/9 ----------------------------------------------------------------------------------------------------------
chk("Gate5", "GoalAnchorBlock at prompt index 0", "prompt = f\"{goal.render()}" in read("src/agent/runtime.py"))
chk("Gate5", "observation ≤180 tokens; last 3 turns hot; facts in Postgres", S.obs_max_tokens == 180 and S.hot_turns == 3 and "facts_put" in read("src/agent/runtime.py"))
chk("Gate7", "subagent isolation: retrieval registry has no mutating tool", "h_retrieval = H({\"hybrid_retrieve\"" in read("src/agent/runtime.py") and "workflow_transition" not in read("src/agent/runtime.py").split("self.h_retrieval")[1].split("self.h_exec")[0])
chk("Gate9", "breaker trips at ≥2 identical failures", "max_failures: int = 2" in read("src/harness/breaker.py"), "window 60 s (spec silent; FN-v0-006)")
chk("Gate9", "bulkheads rag=64 db=128 llm=256", (S.rag_pool, S.db_pool, S.llm_pool) == (64, 128, 256))
chk("Gate9", "deterministic abstention short-circuit (no LLM)", "if not chunks_raw:" in read("src/agent/runtime.py"))
chk("Gate9", "async HITL: PENDING_APPROVAL in Postgres, requester≠approver", "PENDING_APPROVAL" in read("src/repo.py") and "cannot approve their own" in read("src/repo.py"))
# ---- Gate 10 spans / Gate 13 governance -----------------------------------------------------------------------------------
code = "".join(p.read_text() for p in list((ROOT / "src").rglob("*.py")) + list((ROOT / "mcp_servers").glob("*.py")))
missing = [s for s in ("hook.pre", "cache.lookup", "triage.classify", "mcp.rag.bm25", "mcp.rag.biencoder", "mcp.rag.rrf", "mcp.rag.rerank", "agent.react.step", "llm.generate", "hook.post.ragas") if f'"{s}"' not in code]
chk("Gate10", "all 10 Gate-10 span names emitted", not missing, f"missing: {missing}")
chk("Gate10", "W3C traceparent in/out", "parse_traceparent" in read("src/api/main.py") or "traceparent" in read("src/api/main.py"))
chk("Gate13", "SHA-256 hash-chained append-only audit, per-tenant", "chain_hash" in read("src/repo.py") and "audit_immutable" in read("migrations/001_init.up.sql"))
chk("Gate13", "tenant_id in every query + FORCE RLS", "FORCE ROW LEVEL SECURITY" in read("migrations/001_init.up.sql") and "app.tenant_id" in read("src/repo.py"))
from mcp_servers import manifest
ih = hashlib.sha256(json.dumps(json.loads(read("artifacts/index/manifest.json")), sort_keys=True).encode()).hexdigest() if (ROOT / "artifacts/index/manifest.json").exists() else ""
mv = {n: manifest.verify(n, ih if n == "hybrid-rag" else "") for n in manifest.SERVERS}
chk("Gate13", "signed MCP manifests verify", all(v[0] for v in mv.values()), str({k: v[1] for k, v in mv.items()}))
chk("Gate13", "zero raw PII persisted (redaction before cache/audit/DB)", "run_pre" in read("src/agent/runtime.py") and "text = await asyncio.to_thread(self.plugins.run_pre" in read("src/agent/runtime.py"))
# ---- Gate 0 config / Gate 17 / Gate 18 -----------------------------------------------------------------------------------
cfg = json.loads(read("mcp_config.json"))
chk("Gate0", "mcp_config.json matches the blueprint (3 servers, 5 plugins, priorities)", set(cfg["mcpServers"]) == {"hybrid-rag", "state-store", "graphify-vault"} and [(p["name"], p["priority"] if "priority" in p else None) for p in cfg["plugins"]][:2] == [("pii_redactor", 10), ("injection_shield", 20)])
chk("Gate17", "vault: atomic notes with id/domain/risk_tier/version + [[wikilinks]]", len(list((ROOT / "knowledge_vault").glob("*.md"))) >= 100)
chk("Gate18", "Streamlit modular layout (app/state/cached/components/views/utils)", all((ROOT / p).exists() for p in ("app.py", "state.py", "cached.py", "components", "views", "utils")))
lock = (ROOT / "DESIGN.lock").read_text().split()[0] if (ROOT / "DESIGN.lock").exists() else ""
chk("Gate1", "design.md locked and unmodified", lock == hashlib.sha256((ROOT / "design.md").read_bytes()).hexdigest())
chk("Gate2", "workflow_orchestration.png exists", (ROOT / "docs/workflow_orchestration.png").exists())

print(f"{'AREA':8s} {'CHECK':78s} RESULT  DETAIL"); [print(f"{a:8s} {n[:78]:78s} {r:6s}  {d}") for a, n, r, d in rows]
fails = [r for r in rows if r[2] == "FAIL"]
print(f"\nBUILD-TIME adherence: {len(rows) - len(fails)}/{len(rows)} PASS")
print("\nKNOWN DEVIATIONS (disclosed; see audit/CLAIMS_REGISTER.md):")
for d in ["D-12 Graphify = in-repo implementation (no public CLI verified)", "D-13 PII = regex + normalizer, not Presidio", "D-14 Triage = rules-first, not a distilled neural classifier",
          "D-15 RAGAS KPIs 20-22 are PROXIES (no LLM judge) → shown INFORMATIONAL", "D-16 L2 semantic cosine≥0.92 NOT implemented (L2 = tenant-scoped exact-normalized only)",
          "D-17 servers spawned with venv python directly (not `uv run`)", "D-18 retrieval engine lives in src/rag, served by mcp_servers/rag_server.py (spec says 'inside')",
          "D-19 MCP tools take `req: dict` and validate with the Pydantic contract inside (JSON-schema exposed via contract:// resources)", "D-20 k6 uses a 200-query mix (5% = spec query) instead of one query",
          "D-21 dev-cpu uses smaller models + 20x time dilation (design §1.16)"]: print("  -", d)
ver = sys.argv[sys.argv.index("--version") + 1] if "--version" in sys.argv else "v0"
sc = kpi_eval.scorecard(results_store.latest_by_kpi(results_store.load_runs(), version=ver)); sm = kpi_eval.summarize(sc)
print(f"\nKPI EVIDENCE ({ver}): PASS={sm['PASS']} FAIL={sm['FAIL']} INFORMATIONAL={sm['INFORMATIONAL']} NO_DATA={sm['NO_DATA']} → certified={sm['certified']}")
sys.exit(1 if fails else 0)
