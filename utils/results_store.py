"""Append-only KPI run store (``artifacts/kpi_runs.jsonl``). Pure Python.

Every eval/bench script appends records through :func:`record_run`; the workbench only reads.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

STORE = Path(os.environ.get("KPI_STORE", "artifacts/kpi_runs.jsonl"))


def new_run_id(prefix: str = "run") -> str:
    """Return a short unique run id."""
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def record_run(kpi_id: int, value: float, *, n: int, run_id: str, version: str, rung: str,
               profile: str, artifact: str, extra: dict[str, Any] | None = None, store: Path | None = None) -> dict:
    """Append one KPI measurement with its evidence fields and return the record."""
    rec = {**(extra or {}), "kpi_id": kpi_id, "value": value, "n": n, "run_id": run_id, "version": version, "rung": rung,
           "profile": profile, "artifact": artifact, "ts": time.time()}
    path = store or STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def load_runs(store: Path | None = None) -> list[dict]:
    """Load all records (empty list if the store does not exist)."""
    path = store or STORE
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def latest_by_kpi(runs: list[dict], version: str | None = None, profile: str | None = None) -> dict[int, dict]:
    """Return the most recent record per KPI id, optionally filtered by version/profile."""
    best: dict[int, dict] = {}
    for r in runs:
        if version and r.get("version") != version:
            continue
        if profile and r.get("profile") != profile:
            continue
        k = r["kpi_id"]
        if k not in best or r["ts"] >= best[k]["ts"]:
            best[k] = r
    return best
