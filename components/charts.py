"""Reusable Plotly chart builders (render-only)."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

SLA_LOW, SLA_HIGH = 200, 600


def latency_histogram(samples_ms: list[float], p50: float, p95: float, p99: float) -> None:
    """Latency histogram with p50/p95/p99 markers and the 200–600 ms SLA band."""
    fig = go.Figure(go.Histogram(x=samples_ms, nbinsx=60, marker_color="#4C78A8"))
    fig.add_vrect(x0=SLA_LOW, x1=SLA_HIGH, fillcolor="#2E7D32", opacity=0.08, line_width=0, annotation_text="200–600 ms SLA")
    for v, lbl in ((p50, "p50"), (p95, "p95"), (p99, "p99")):
        fig.add_vline(x=v, line_dash="dash", annotation_text=f"{lbl} {v:.0f}")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="ms", yaxis_title="requests")
    st.plotly_chart(fig, use_container_width=True)


def gauge(value: float | None, title: str, threshold: float, lo: float = 0.0, hi: float = 1.0) -> None:
    """Gauge with a threshold marker; ``None`` renders an explicit 'no data' note."""
    if value is None:
        st.metric(title, "no data")
        return
    fig = go.Figure(go.Indicator(mode="gauge+number", value=value, title={"text": title},
                                 gauge={"axis": {"range": [lo, hi]}, "threshold": {"line": {"color": "black", "width": 3}, "value": threshold},
                                        "bar": {"color": "#2E7D32" if value >= threshold else "#C62828"}}))
    fig.update_layout(height=220, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)


def bar(labels: list[str], values: list[float], title: str) -> None:
    """Simple bar chart."""
    fig = go.Figure(go.Bar(x=labels, y=values))
    fig.update_layout(title=title, height=300, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)
