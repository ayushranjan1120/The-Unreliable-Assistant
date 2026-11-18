# FN-<version>-<NNN> — <one-line symptom>

| Field | Value |
|---|---|
| Build version under test | vX.Y |
| Rung / offered load | L? / N VUs / ~RPS / think time |
| Date / run id | |
| Severity | Sev-1 (isolation/HITL/PII) · Sev-2 (accuracy gate) · Sev-3 (latency/err) · Sev-4 (minor) |
| Outcome | soft-miss (re-run same rung) · hard-fail (rollback + new version) · abort |
| Rollback executed | Y/N — target version, time taken |

## Symptom
What was observed, with numbers.

## KPI(s) violated
| KPI | Threshold | Observed | Load level where it first broke |
|---|---|---|---|

## Evidence
k6/JMeter summary path, Grafana snapshot, 20 slowest trace ids, error samples, degraded-flag counts, injector CPU (validity check).

## Root cause (5 whys)
1.
2.
3.

## Blast radius
Tenants / paths / KPIs affected. Any cross-tenant or Tier-3 impact? (if yes: Sev-1)

## Fix
Change made, files, config/lever. Accuracy cost of the fix (re-measured KPI-20..24):

## Regression test added
Path + what it proves. Must fail before the fix and pass after.

## Verification
Rung re-run result, KPIs now passing (numbers).

## Design impact
Does design.md need a version bump? (spec conflict / budget / FMEA / ADR)  Y/N — details.

## Audit link
audit/AUDIT-<date>.md finding id (if applicable).

## Fixed in
vX.Y+1
