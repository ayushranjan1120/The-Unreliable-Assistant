# If you are stuck — what to provide, and what to run

**Rule 1:** never paste API keys or passwords into chat or GitHub. Put them in `.env` (gitignored) and **save the file** (Ctrl+S). If a secret was pasted anywhere, rotate it.
**Rule 2:** when something fails, run `scripts/diagnose.sh > diag.txt`, skim it, and share `diag.txt`. It masks secrets and prints env var *names* only.

## What the project needs from you
| Need | Where | Needed for | Status |
|---|---|---|---|
| `GROQ_API_KEY` | `.env` | Real LLM generation (ADR-7 bake-off, accuracy KPIs) | ✅ set |
| `PG_ADMIN_DSN` (Neon owner string) | `.env` | One-time migrations (`python -m scripts.neon_setup`) | ✅ set — **rotate after testing** |
| `PG_DSN` | `.env` (auto-written) | App database as the low-privilege `ua_app` role | ✅ generated |
| Redis | local `apt install redis-server` or `REDIS_URL` | Cache/breaker sharing (optional; falls back to memory) | local |
| GPU host (type/count) | infra | Certifying KPI-08/09/25 and the 5K→50K ladder | ❌ **needed for certification** |
| Distributed load injectors | infra | L2–L6 runs (k6 operator / JMeter workers) | ❌ **needed for certification** |
| Database co-located with the app | infra | Latency KPIs (Neon from this VM = ~430 ms RTT) | ❌ needed for latency numbers |
| OpenRouter policy/credit | openrouter.ai | Optional second provider in the failover chain | ⚠️ blocked by your account guardrail |
| GitHub push | VS Code Source Control | Publishing | ✅ you push; I commit |
| Decision: allow routing hard queries to a larger model? | design ADR-7 | Closing the accuracy gap if the small model fails on multi-hop | ⏳ open |

## Symptom → cause → fix → what to send
| Symptom | Likely cause | Fix | Send me |
|---|---|---|---|
| `API failed to start` | MCP manifest/index mismatch, port busy, missing models | `scripts/dev_stack.sh restart` (re-signs manifests); `ss -ltnp \| grep -E '8000\|8081'` | `diag.txt` |
| Sidebar says "offline" | Backend not running / wrong URL | `scripts/dev_stack.sh start`; set URL in sidebar | `diag.txt` |
| Every answer is "I don't have enough information" | Circuit breaker open, reranker timing out, or stale cache | wait 60 s or `redis-cli flushdb`; check `TIME_SCALE`; admin → Breaker tab | Tab 5 breaker table + `diag.txt` |
| `GROQ_API_KEY loaded: False` | `.env` not saved / wrong folder | save `/home/labuser/Hackathon/.env`; no spaces around `=` | `diag.txt` (shows `<empty>`/`<set>`) |
| Groq 401/403/404 | bad key or model not on your account | `curl -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models` | the model list (no key) |
| OpenRouter 404 "guardrail" | account privacy/guardrail blocks the model | relax settings at openrouter.ai or drop it from the chain | the error text |
| DB `permission denied` / RLS test fails | app connected as owner (bypasses RLS) | re-run `python -m scripts.neon_setup` | its output |
| HITL "TOOL_TIMEOUT" | remote DB latency vs step budget | `STATE_TIMEOUT_MS=15000` in `.env` | `diag.txt` |
| Load test shows `EOF`/connection reset | server keep-alive shorter than think time | uvicorn `--timeout-keep-alive 75` (already in `dev_stack.sh`); same on Envoy/NGINX | k6 summary |
| Everything slow (seconds) | CPU-only host: reranker ≈0.7 s | expected on `dev-cpu`; certify on GPU | — |
| `git commit` says "who you are" | identity unset | `git config user.name/email` (repo-local) | — |
| Streamlit tab shows "no data" | backend down or metric never recorded | that is by design (never fabricated) | which tab |

## Decisions I may ask you for
Model choice after the bake-off · whether to bump the design (D1.x) for complexity routing · GPU sizing · which cloud region (co-locate DB + app) · production secrets handling (KMS vs `.env`).
