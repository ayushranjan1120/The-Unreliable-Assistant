"""Tab 2 — 3-Stage Hybrid RAG Lab: per-stage score comparison with live sliders."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import cached
import state
from components import cards


def render(ctx: state.Ctx) -> None:
    """Render Tab 2."""
    st.subheader("3-Stage Hybrid RAG Lab")
    p = st.session_state["lab_params"]
    c1, c2, c3, c4 = st.columns(4)
    p["k1"] = c1.slider("BM25 k1", 0.5, 3.0, p["k1"], 0.05)
    p["b"] = c2.slider("BM25 b", 0.0, 1.0, p["b"], 0.05)
    p["rrf_k"] = c3.slider("RRF k", 1, 200, p["rrf_k"], 1)
    p["threshold"] = c4.slider("Cross-encoder threshold", 0.0, 1.0, p["threshold"], 0.01)
    q = st.text_input("Query", "What is the storage requirement for SKU-099?", key="lab_q")
    if not st.button("Run retrieval", type="primary"):
        return
    with st.status("Running 3-stage retrieval …") as s:
        r = cached.backend(ctx.backend_url).post("/api/v1/admin/rag_lab", {"tenant_id": ctx.tenant_id, "query": q, **p}, ctx.tenant_id)
        if not r.ok:
            s.update(label="failed", state="error")
            cards.no_data(r.error)
            return
        s.update(label="done", state="complete")
    d = r.data
    ms = d.get("stage_ms", {})
    cards.metric_row([("BM25 ms", ms.get("bm25"), "KPI-07 ≤ 12"), ("Dense ms", ms.get("dense"), "KPI-08 ≤ 25"),
                      ("RRF ms", ms.get("rrf"), "≤ 3"), ("Rerank ms", ms.get("rerank"), "KPI-09 ≤ 45"),
                      ("Top score", d.get("top_score"), "abstain if < threshold")])
    if d.get("abstain"):
        st.warning("Deterministic abstention: top cross-encoder score below threshold; no LLM call.")
    df = pd.DataFrame(d.get("candidates", []))
    if df.empty:
        cards.no_data("No candidates returned.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)
