# CLAUDE.md — The Unreliable Assistant → Enterprise Grade

Operating rules for Claude Code in this repo. **`design.md` is the source of truth** (platform-agnostic, for any tool). This file only says how to work here; it never overrides `design.md`. If they disagree, stop and raise it.

## Prime objective
Reduce end-to-end latency and raise answer accuracy **at 50,000 concurrent VUs** (p50 ≤ 240, p95 ≤ 450, p99 ≤ 590 ms; faithfulness ≥ 0.92). Prefer changes that help this over anything else. Never buy latency by silently dropping accuracy: degradation levers (design §1.10) must be flagged `degraded=true` and re-measured.

## Current phase
Check `VERSIONS.md` for the current version/rung before acting. Phases: **PLAN (design.md review → lock)** → **BUILD v0** → **LADDER L0…L6** → **failure notes → vX.Y+1** → **post-hardening audit intake**.

## HARD GATE (enforced by a hook)
- No application code until `design.md` is reviewed and locked: `scripts/lock_design.sh --i-reviewed` creates `DESIGN.lock` (sha256).
- `.claude/hooks/gate1_guard.py` (PreToolUse on Write/Edit) blocks writes outside docs/notes paths while unlocked, or when `design.md` changed since lock.
- Hook limits: it does not see Bash redirects (`cat > app.py`). Do not route around it. Never edit `.claude/` or `DESIGN.lock` to bypass it.
- Changing a locked design: `scripts/unlock_design.sh "<reason>"` → edit → bump design version (`D1.x`) → re-review → re-lock.

## Non-negotiable engineering rules
1. **Tenant isolation:** `tenant_id` in every SQL statement and every Redis key. Build keys only via the `keys.py` helper; use only the repository layer for SQL. No raw string keys / raw SQL elsewhere.
2. **No raw PII** persisted or logged (logs, traces, cache, DB, fixtures). Redact first.
3. **Every tool call goes through `ToolExecutionHarness`** (validate → auto-repair → RBAC → idempotency → breaker → dispatch → compress). No direct MCP calls.
4. **Bounded everything:** loop ≤ 5 iterations / 120 ms step / 450 ms total; no unbounded retries or recursion; every external call has a timeout.
5. **Fail-closed** for PII/injection plugins and Tier-3. **Fail-soft with a flag** for retrieval/cache/LLM (design §1.6).
6. **Tier-3 never executes without an approval record.** No autonomous clinical/financial/legal action.
7. **Abstain rather than guess:** cross-encoder top score < threshold → deterministic abstention, no LLM.
8. **No fabricated telemetry.** UI/scorecards show "no data" when a metric is absent, never a placeholder pass.
9. Pydantic v2 `strict=True, extra="forbid"` on every contract. Python 3.11, async I/O only on the request path.
10. Don't mock away the thing a KPI measures (e.g. don't stub the reranker to pass KPI-09).

## Build → test → fix loop
1. Build in the order in design §1.14 (step 0 = Streamlit shell, so the UI runs from the start); each step must have its L0 KPIs green before the next.
2. Climb the ladder one rung at a time: L0 component → L1 100 VU + chaos + red team + **rollback drill** → L2 **5K** → L3 **10K** → L4 25K → L5 **50K** → L6 1-hr soak. Never skip a rung. Load shape and think time: design §0 SC-08, §1.11.
3. On any miss: **write a failure note first** (`failure_notes/TEMPLATE.md` → `FN-<ver>-<NNN>.md`), reproduce with a failing test, then fix.
4. Soft miss (latency only, ≤10% over) → tune/scale, re-run same rung. Hard fail (accuracy, isolation, HITL, loop-escape, 5xx, crash) → **rollback**, new version, re-run from the lowest affected rung.
5. Tag and record in `VERSIONS.md` (`vX.Y`, rung reached, KPIs failed, notes addressed, rollback target). Failed attempts are tagged `vX.Y-L<n>-FAIL` and kept.
6. Rollback before fixing if an abort condition fired (design §1.11). Rollback is routine, not a failure — but always followed by a failure note.
7. Stop and ask before: unlocking design, raising a KPI threshold, changing the golden set, running a load test above the rung just earned, or any irreversible/outward-facing action.

## Post-hardening audit intake
When an audit report arrives: save unmodified to `audit/AUDIT-<date>.md` → classify each finding (confirmed / spec-conflict / design-gap / not-reproducible / won't-fix) → failing repro test → failure note → fix → new version → re-run lowest affected rung → give a finding-by-finding disposition table. Rank by impact on p95/p99 and accuracy at 50K.

## Decisions already made (owner, 2026-11-13) — do not re-litigate
- SC-08: certification load uses **5 s think time** (~9.4K RPS at 50K VUs); literal `sleep(1)` is a separate overload/shedding test.
- SC-09: **two-tier cache** — L1 per-user exact (spec key) + L2 tenant-scoped, `shareable` answers only. Tenant isolation never relaxed.
- SC-01..07, 10..14: resolutions in design §0 stand as written.
- `llm.generate`: **small fast model, no accuracy compromise** (design §1.4.1, ADR-7). Chosen by bake-off against the accuracy-parity gate; if none passes, do not relax the gate — write a failure note.
- UI: **Streamlit workbench, modular** (design §1.13.1). Open item: GPU type/count (sized at L2).

## Three iterations & audit adherence (owner, 2026-11-13)
- Delivery = **3 iterations**: I1 `v0` → Version Report → fix → I2 `v0.1` → VR → fix → I3 `v0.2` → VR + final audit reconciliation (design §1.11.1). Reports go in `failure_notes/reports/VR-<version>.md` (template there). Do not add a 4th version without asking.
- `audit/AUDIT-2026-11-13.md` is a **claims document, not evidence** — no such build exists. Never mark a KPI/claim PASS from it. Gates are the design thresholds; the report's numbers are reference expectations (design §1.15).
- Every KPI result needs: value, N, run id, version, rung, artifact path. No artifact = "no data" = FAIL. Absolute claims are written "0 observed in N trials (95% upper bound ≈ 3/N)", never "100% proven".
- Each VR must update `audit/CLAIMS_REGISTER.md` (Status column) and list all 36 KPI values. Append new contradictions as D-12, D-13 …
- Build-time adherence (tag **D/B** in the register): config values, pipeline order (BM25 ‖ dense → RRF → rerank), key scheme, span names (Gate 10, with KPI aliases), RAGAS reported per stratum with all five metrics. Test-time (tag **T**): everything numeric.
- Measurement definitions fixed up front: KPI-26 = input-token reduction on the same 250 golden queries; KPI-29 = full cost with cache hits separate. Attribute any cost drop (tokens vs model vs cache).

## Streamlit workbench rules (apply to every UI change)
- Structure: `app.py` (entry/routing only) · `state.py` (all `st.session_state`) · `cached.py` (all `@st.cache_*`) · `components/` (render-only widgets) · `views/` (one module per tab, `render()`) · `utils/` (pure Python, **never imports streamlit**).
- Layout: `layout="wide"`; global controls in the sidebar; `st.metric` cards, `st.divider()`, bordered containers; `st.status`/`st.spinner` for long ops; `st.toast`/`st.info`/`st.error` for feedback.
- Add type hints + one-line docstrings to new helpers. Pass `tenant_id` into cached functions. Never cache live telemetry past its refresh interval.
- The app must start with the backend down and show "no data" — never fake values. Verify with `streamlit.testing.v1.AppTest`, and with `streamlit run app.py` + `/_stcore/health`.
- Refactors must preserve all six tabs' behaviour (AppTest suite is the proof).

## Commands (planned; created during BUILD)
```
python3 docs/build_diagrams.py        # regenerate workflow_orchestration.png + release_ladder.png (needs graphviz + dot)
scripts/lock_design.sh --i-reviewed   # lock design
uv run pytest -q                      # unit + component benches
uv run python eval/ragas_run.py       # golden-250 RAGAS gates
uv run python eval/redteam_100/run.py # red team
load_tests/ladder.sh <rung>           # k6 (+JMeter L4+) for a rung
streamlit run app.py                  # 6-tab workbench (port 8501; must boot with backend down)
```

## Style & conventions
- Layout: design §1.14 (incl. Streamlit modules). Contracts live in `src/contracts/`; one module owns Redis keys, one owns SQL.
- Every span name must match design §1.10. Never put raw PII in span attributes.
- Keep PRs/commits small: one failure note ↔ one fix ↔ one regression test.
- When unsure about a requirement, quote the Gate/KPI id from `design.md` in your question.
