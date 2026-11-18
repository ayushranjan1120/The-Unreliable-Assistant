"""The only module that applies ``@st.cache_*`` to ``utils/`` functions (design §1.13.1 rule 2/5).

Live telemetry uses a short TTL; static files a longer one. ``tenant_id`` is always an argument
of tenant-scoped cached calls so cache entries cannot leak across tenants.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from utils import design_parser, kpi_eval, results_store
from utils.api_client import ApiResult, BackendClient

ROOT = Path(__file__).resolve().parent


@st.cache_resource
def backend(url: str) -> BackendClient:
    """Shared backend client per URL."""
    return BackendClient(url)


@st.cache_data(ttl=30)
def design_checklist() -> list[tuple[bool, str]]:
    """Parsed review checklist from design.md."""
    return design_parser.parse_checklist(ROOT / "design.md")


@st.cache_data(ttl=30)
def lock_status() -> dict:
    """Design lock state."""
    return design_parser.lock_status(ROOT)


@st.cache_data(ttl=30)
def versions_ledger() -> list[dict]:
    """Build ledger rows from VERSIONS.md."""
    return design_parser.parse_versions_ledger(ROOT / "VERSIONS.md")


@st.cache_data(ttl=5)
def kpi_runs() -> list[dict]:
    """All recorded KPI runs (short TTL: live telemetry)."""
    return results_store.load_runs(ROOT / "artifacts" / "kpi_runs.jsonl")


@st.cache_data(ttl=5)
def scorecard(version: str | None, profile: str | None) -> list[dict]:
    """36-KPI scorecard rows for a version/profile filter."""
    runs = results_store.latest_by_kpi(kpi_runs(), version=version, profile=profile)
    return kpi_eval.scorecard(runs)


def backend_health(url: str) -> ApiResult:
    """Uncached liveness check (must always be live)."""
    return backend(url).health()


def backend_get(url: str, path: str, **params: Any) -> ApiResult:
    """Uncached admin GET (live)."""
    return backend(url).get(path, **params)
