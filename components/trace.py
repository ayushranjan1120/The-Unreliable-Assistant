"""Span waterfall and ReAct accordion widgets."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st


def span_waterfall(spans: list[dict]) -> None:
    """Render ``[{name, start_ms, dur_ms}]`` as a horizontal waterfall; empty → 'no data'."""
    if not spans:
        st.info("no data — run a query to capture a trace")
        return
    fig = go.Figure()
    for s in spans:
        fig.add_trace(go.Bar(y=[s["name"]], x=[s["dur_ms"]], base=[s["start_ms"]], orientation="h", showlegend=False,
                             hovertemplate=f"{s['name']}: {s['dur_ms']:.1f} ms<extra></extra>"))
    fig.update_layout(height=max(220, 28 * len(spans)), margin=dict(l=10, r=10, t=10, b=10), xaxis_title="ms", yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)


def react_accordion(steps: list[dict]) -> None:
    """Render ReAct steps ``[{n, state, tool, observation, ms}]`` as expanders."""
    if not steps:
        st.info("no data — no ReAct trace captured yet")
        return
    for s in steps:
        with st.expander(f"Step {s.get('n')} · {s.get('state')} · {s.get('tool') or '—'} · {s.get('ms', 0):.0f} ms"):
            st.code(str(s.get("observation", "")), language="text")
