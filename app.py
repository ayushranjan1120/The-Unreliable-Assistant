"""Streamlit developer workbench entry point (design §1.13): page config, routing, top-level layout only."""
from __future__ import annotations

import streamlit as st

import state
from components import sidebar
from views import architecture, mcp_inspector, memory_tokens, rag_lab, redteam, scorecard

TABS = [
    ("1 · Architecture & Workflow", architecture.render),
    ("2 · 3-Stage RAG Lab", rag_lab.render),
    ("3 · MCP & Plugins", mcp_inspector.render),
    ("4 · Memory & Graphify Tokens", memory_tokens.render),
    ("5 · Red Team & Breaker", redteam.render),
    ("6 · 36-KPI Scorecard & 50K Load", scorecard.render),
]


def main() -> None:
    """Configure the page, build the sidebar, and render the six tabs."""
    st.set_page_config(page_title="Unreliable Assistant — Workbench", page_icon="🛠️", layout="wide")
    state.init_state()
    ctx = sidebar.render()
    st.title("The Unreliable Assistant → Enterprise Grade")
    st.caption("Developer control plane · dev-only · shows 'no data' rather than fabricated values")
    for tab, (_, render) in zip(st.tabs([t for t, _ in TABS]), TABS):
        with tab:
            try:
                render(ctx)
            except Exception as e:  # a failing view must not take down the other tabs
                st.error(f"View failed: {type(e).__name__}: {e}")
    if st.session_state["auto_refresh"]:
        st.fragment(run_every=st.session_state["refresh_s"])(lambda: None)()


main()
