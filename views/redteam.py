"""Tab 5 — Red Team & Circuit Breaker: one-click adversarial runs and breaker controls."""
from __future__ import annotations

import streamlit as st

import cached
import state
from components import cards, tables

SCENARIOS = {
    "direct": "Direct jailbreak / system-prompt extraction",
    "indirect": "Indirect injection hidden in retrieved chunk",
    "crescendo": "Multi-turn crescendo",
    "pii": "Obfuscated PII / name[at]domain / homoglyph",
    "fuzz": "Tool-argument overflow / SQLi fuzz",
    "sku_loop": "Infinite SKU retry loop (breaker)",
    "tenant_probe": "Cross-tenant cache probe",
}


def render(ctx: state.Ctx) -> None:
    """Render Tab 5."""
    st.subheader("Red Team & Circuit Breaker")
    left, right = st.columns([3, 2])
    with left:
        with st.container(border=True):
            sel = st.multiselect("Scenarios", list(SCENARIOS), default=list(SCENARIOS), format_func=lambda k: SCENARIOS[k])
            n = st.slider("Mutations per seed prompt", 1, 25, 5)
            if st.button("Run selected", type="primary", disabled=not sel):
                with st.status("Running adversarial suite …", expanded=True) as s:
                    r = cached.backend(ctx.backend_url).post("/api/v1/admin/redteam/run", {"scenarios": sel, "mutations": n}, ctx.tenant_id)
                    if r.ok:
                        s.update(label="complete", state="complete")
                        st.session_state["redteam_last"] = r.data
                        st.toast("Red-team run finished", icon="🛡️")
                    else:
                        s.update(label="failed", state="error")
                        st.error(r.error)
        last = st.session_state.get("redteam_last")
        if last:
            cards.metric_row([("Executions", last["executions"], "N trials"), ("Misses", last["misses"], "must be 0"),
                              ("95% upper bound", f"{last['upper_bound_pct']:.3f}%", "≈ 3/N"), ("Cross-tenant leaks", last["leaks"], "KPI-28")])
            tables.simple_table(last["by_scenario"], "no per-scenario data")
        else:
            cards.no_data("No red-team run yet.")
    with right:
        with st.container(border=True):
            st.markdown("**Circuit breaker**")
            b = cached.backend_get(ctx.backend_url, "/api/v1/admin/breaker")
            if not b.ok:
                cards.no_data(b.error)
                return
            tables.simple_table(b.data.get("entries", []), "no breaker entries (all closed)")
            c1, c2 = st.columns(2)
            if c1.button("Trip (SKU loop)"):
                r = cached.backend(ctx.backend_url).post("/api/v1/admin/breaker/trip", {}, ctx.tenant_id)
                (st.toast if r.ok else st.error)("tripped" if r.ok else r.error)
            if c2.button("Reset all"):
                r = cached.backend(ctx.backend_url).post("/api/v1/admin/breaker/reset", {}, ctx.tenant_id)
                (st.toast if r.ok else st.error)("reset" if r.ok else r.error)
