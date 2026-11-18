"""Reusable metric/status cards (render-only)."""
from __future__ import annotations

import streamlit as st

NO_DATA = "no data"


def metric_row(items: list[tuple[str, str | float | None, str | None]]) -> None:
    """Render ``[(label, value, help)]`` as equal-width ``st.metric`` cards; ``None`` shows 'no data'."""
    cols = st.columns(len(items))
    for col, (label, value, help_) in zip(cols, items):
        col.metric(label, NO_DATA if value is None else value, help=help_)


def no_data(message: str = "Backend unavailable — no data.") -> None:
    """Standard empty-state notice (never a placeholder pass)."""
    st.info(message, icon="ℹ️")


def status_badge(status: str) -> str:
    """Return an emoji-prefixed status string for tables."""
    return {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "NO_DATA": "⚪ no data", "NO_EVIDENCE": "🟠 no evidence",
            "INFORMATIONAL": "🔵 info (not certifiable)"}.get(status, status)
