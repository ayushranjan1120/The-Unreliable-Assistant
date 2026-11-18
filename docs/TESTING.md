# TESTING.md — how to test everything, step by step

**Rule of thumb:** *pytest + eval scripts + k6/JMeter produce the evidence; Streamlit only displays it.* Never certify from a screenshot. Every KPI needs a value, N, run id and artifact (design §1.15).

## 0. Prerequisites (once)
```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r <(python3 -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))") pytest pytest-asyncio
sudo apt-get install -y redis-server postgresql postgresql-contrib postgresql-18-pgvector   # then create role ua_app/db ua + `python -m scripts.migrate up`
python eval/make_corpus.py                                  # vault + golden-250 + labeled sets (seeded, deterministic)
python -m mcp_servers.graphify_build ./src ./knowledge_vault --output artifacts/graph.json
# export ONNX models once (see FN/VR notes): artifacts/models/{bi,rerank,nli,inj}; then build the index:
python -c "from src.config import *; from src.rag.index import build_index; build_index(get_settings())"
cp .env.example .env   # add GROQ_API_KEY / OPENROUTER_API_KEY here — NEVER in chat, NEVER committed
```

## 0b. Database without Docker/local Postgres (Neon)
Docker is **not used anywhere**. Put the Neon *owner* connection string in `.env` as `PG_ADMIN_DSN`, then `python -m scripts.neon_setup` — it migrates, creates a `NOBYPASSRLS` app role `ua_app`, writes `PG_DSN` to `.env` and **proves tenant RLS** (own rows visible, other tenant's invisible, cross-tenant write blocked). Redis is optional: without it the cache/breaker fall back to per-process memory. Remote-DB caveat: every state-store call pays the network RTT (427 ms measured from this VM to Neon us-east-1), so latency KPIs are only meaningful with the DB co-located with the app; use `STATE_TIMEOUT_MS` for functional tests.

## 1. Static + unit tests (≈20 s, no servers)
`python -m pytest -q` → expect 26 tests. Covers: no raw Redis keys/SQL outside `keys.py`/`repo.py`, strict contracts, BM25 == `rank_bm25`, RRF, chunker, cache tenant assertion, fail-closed plugins, PII, manifest tamper, audit-chain tamper + RLS, harness (repair/RBAC/idempotency/HITL/breaker), workbench boots with backend down.

## 2. Spec adherence (audit "D/B" items)
`python -m scripts.audit_check` → expect `BUILD-TIME adherence: 28/28` + the disclosed deviations list + KPI evidence summary.

## 3. L0 component benches (record KPI evidence)
| Command | KPIs |
|---|---|
| `python -m eval.ir_bench` (set `TIME_SCALE=25`) | 07–13 |
| `python -m eval.guard_bench` | 01, 02, 03 (holdout), 06 |
| `python -m eval.triage_bench` | 04, 05 |
| `python -m eval.harness_bench` | 14, 15 |

## 4. Start the stack, then L1 system tests
```bash
scripts/dev_stack.sh start          # re-signs manifests, starts state-store :8081 and API :8000 (never use pkill -f)
python -m eval.system_tests         # 16 checks: tenant header, Tier-3 HITL flow, RBAC, audit chain, breaker, 120-request cross-tenant storm → KPI 14,16,19,28
python -m eval.golden_eval          # golden-250 end to end (≈6-10 min on CPU) → KPI 17,20-24,26,29 (20-22 are PROXIES)
python -m scripts.rollback_drill    # L1 rollback drill (4/5 layers; code rollback needs a git tag)
```

## 5. Streamlit workbench (live inspection)
`streamlit run app.py` → http://localhost:8501 (set the backend URL in the sidebar). Walk the tabs: **1** type a query, see route/ReAct steps/HITL queue (approve as a *different* user id); **2** move k1/b/RRF-k/threshold sliders; **3** toggle plugins, inspect JSON-RPC payloads; **4** Graphify subgraph + token gauge; **5** run the adversarial scenarios, trip/reset the breaker; **6** the 36-KPI scorecard (NO DATA stays NO DATA). Automated check: `pytest tests/test_workbench.py`.

## 6. Load ladder (only the smoke is valid on this 4-vCPU host)
`SCALE=4 load_tests/ladder.sh SMOKE --record v0` → labelled NOT CERTIFIABLE. Real rungs L2 5K → L3 10K → L4 25K → L5 50K → L6 soak run on target infra with distributed injectors: `RUNG=L2 THINK_S=5 load_tests/ladder.sh L2 --record v0.1` etc. JMeter: `jmeter -n -t load_tests/jmeter_50k_plan.jmx -R worker1,…,worker10 -l results_50k.jtl -e -o dashboard_50k/`. Overload scenario: `THINK_S=1`.

## 7. Reports
`python -m scripts.make_vr v0` → `failure_notes/reports/VR-v0.md` (all 36 KPIs from evidence). Failure → `failure_notes/FN-<ver>-NNN.md` first, then fix.

## Streamlit "200 MB" question
The workbench loads **no models** and takes **no uploads** (the 200 MB figure is Streamlit's default *upload* limit; Community Cloud also caps app resources). Models/index (~1.7 GB) live in `artifacts/` (gitignored) and run in the backend, not in Streamlit. A Cloud-hosted workbench cannot reach `localhost`; it would need a public backend URL + `ADMIN_TOKEN`, and tenant data must not be exposed. Load tests never run through Streamlit.
