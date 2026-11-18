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
| v0.1 (I2) | — | — | — | — | v0 | planned goal: L2–L4 (10K, 25K); not started |
| v0.2 (I3) | — | — | — | — | v0.1 | planned goal: L5 (50K) + L6 soak; not started |
| 2026-11-18 | design D1.0 locked | sha256 4902c991a38d |
