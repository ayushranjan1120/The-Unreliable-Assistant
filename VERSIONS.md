# VERSIONS.md — release & design ledger

Append-only. One row per event. Tags: `vX.Y` = rung passed · `vX.Y-L<n>-FAIL` = failed attempt (kept as evidence).

| Date | Version / event | Detail |
|---|---|---|
| 2026-11-13 | design D1.0-DRAFT created | CLAUDE.md, design.md, workflow_orchestration.png, release_ladder.png |
| 2026-11-13 | design D1.0 (not yet locked) | owner decisions: SC-08 + SC-09 accepted, SC-01..07/10..14 accepted as written, small-model `llm.generate` (ADR-7), modular Streamlit workbench (§1.13.1). Build NOT started. |
| 2026-11-13 | audit report received (`audit/AUDIT-2026-11-13.md`) | treated as unverified claims + targets; 20 claims, 11 discrepancies logged in `audit/CLAIMS_REGISTER.md`; 3-iteration plan v0 → v0.1 → v0.2 added (design §1.11.1, §1.15). Build NOT started. |

## Build ledger (fill from v0 onward)

| Version | Date | Highest rung passed | KPIs failing | Failure notes addressed | Rollback target | Notes |
|---|---|---|---|---|---|---|
| v0 (I1) | 2026-11-18 | L1 partial (L0 benches, 16/16 system checks, rollback 4/5); L2+ NOT RUN | 01,03,06,13,24,30 FAIL; 20-22,08,09,29 informational; 9 no data | FN-v0-001…008 | none (nothing deployed) | built on dev-cpu; nothing certified; VR-v0.md |
| v0.1 (I2) | 2026-11-19 | L0/L1 benches; L2+ NOT RUN | injection (KPI-03), nDCG, latency | FN-v0-002 (partial), FN-v0-001 (partial) | v0 | cache-before-injection ordering; trained LR injection detector; golden run aborted by VM restart |
| v0.2 (I3) | 2026-11-25 | L1 (system, red team, rollback 4/5); L2–L6 NOT RUN (no GPU) | 01, 03, 13, 30 FAIL; 9 no data (scale + 3 unmeasured) | FN-v0-004 closed; FN-v0-005 closed for dev; FN-v0-002/003/006/007 open | v0.1 | 17 pass / 4 fail / 6 info / 9 no data; release candidate, not certified |
| 2026-11-18 | design D1.0 locked | sha256 4902c991a38d |
