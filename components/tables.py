"""Reusable table renderers."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.cards import status_badge


def kpi_table(rows: list[dict]) -> None:
    """Render the KPI scorecard rows with status badges."""
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No KPI rows.")
        return
    df["status"] = df["status"].map(status_badge)
    cols = ["kpi", "name", "target", "value", "n", "status", "rung", "profile", "run_id"]
    st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, use_container_width=True)


def simple_table(rows: list[dict], empty: str = "no data") -> None:
    """Render a list of dicts, or an empty-state message."""
    if not rows:
        st.info(empty)
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
