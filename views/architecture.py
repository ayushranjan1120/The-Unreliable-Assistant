"""Tab 1 — Architecture & Workflow: design checklist, workflow diagram, triage console, ReAct trace, HITL queue."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

import cached
import state
from components import cards, trace
from utils.samples import SAMPLES

ROOT = Path(__file__).resolve().parent.parent


def render(ctx: state.Ctx) -> None:
    """Render Tab 1."""
    st.subheader("Architecture & Workflow")
    lock = cached.lock_status()
    check = cached.design_checklist()
    done = sum(1 for t, _ in check if t)
    cards.metric_row([("Design version", lock["design_version"] or None, None),
                      ("Lock state", lock["state"], lock["hash"] or None),
                      ("Checklist", f"{done}/{len(check)}" if check else None, "Review Checklist in design.md"),
                      ("Build", ctx.version, f"profile {ctx.profile}")])
    left, right = st.columns([3, 2])
    with left:
        with st.container(border=True):
            st.markdown("**Workflow orchestration** (Gate 2, 300 DPI)")
            png = ROOT / "docs" / "workflow_orchestration_web.png"
            if png.exists() or (ROOT / "docs" / "workflow_orchestration.png").exists():
                st.image(str(png), use_container_width=True)
                st.caption("web preview · full 300 DPI original: docs/workflow_orchestration.png · regenerate: `python docs/build_diagrams.py`")
            else:
                st.error("docs/workflow_orchestration.png is missing (Gate 2).")
    with right:
        with st.container(border=True):
            st.markdown("**design.md contract checklist**")
            for ticked, text in check:
                st.checkbox(text, value=ticked, disabled=True, key=f"chk_{hash(text)}")
    st.divider()
    _triage_console(ctx)
    st.divider()
    _hitl_queue(ctx)


def _apply_sample() -> None:
    """Callback: copy the chosen sample query into the query box."""
    pick = st.session_state.get("sample_pick")
    for label, query, _ in SAMPLES:
        if label == pick:
            st.session_state["triage_q"] = query


def _triage_console(ctx: state.Ctx) -> None:
    st.markdown("**Live triage routing console & ReAct trace**")
    st.session_state.setdefault("triage_q", SAMPLES[0][1])
    st.selectbox("Sample queries (each shows a different behaviour)", [x[0] for x in SAMPLES], key="sample_pick", on_change=_apply_sample,
                 help="Pick one to fill the box below, then press Route query.")
    expect = next((e for l, _, e in SAMPLES if l == st.session_state.get("sample_pick")), None)
    if expect:
        st.caption(f"Expected: {expect}")
    q = st.text_input("Query", key="triage_q")
    if st.button("Route query", type="primary"):
        with st.status("Calling /api/v1/agent/query …", expanded=False) as s:
            res = cached.backend(ctx.backend_url).query_debug(ctx.tenant_id, ctx.user_id, q, ctx.session_id)
            if res.ok:
                s.update(label=f"done in {res.latency_ms:.0f} ms", state="complete")
                state.push_trace(res.data)
                st.toast("Query routed", icon="✅")
            else:
                s.update(label="failed", state="error")
                st.error(res.error)
    hist = st.session_state["trace_history"]
    if not hist:
        cards.no_data("No query yet — backend offline or nothing routed.")
        return
    last = hist[-1]
    cards.metric_row([("Route", last.get("route"), None), ("Status", last.get("status"), None),
                      ("Abstained", str(last.get("abstained")), None), ("Degraded", str(last.get("degraded")), None)])
    st.write(last.get("answer") or "—")
    trace.react_accordion(last.get("react_steps", []))


def _hitl_queue(ctx: state.Ctx) -> None:
    st.markdown("**Tier-3 HITL approval queue**")
    approver = st.text_input("Approver user id (must differ from the requester)", "user_reviewer", key="approver_id")
    res = cached.backend_get(ctx.backend_url, "/api/v1/admin/hitl", tenant_id=ctx.tenant_id)
    if not res.ok:
        cards.no_data()
        return
    items = res.data.get("pending", [])
    if not items:
        st.success("No pending approvals.")
        return
    for it in items:
        with st.container(border=True):
            st.write(f"`{it['approval_id']}` · requester `{it['requester']}` · {it['action']}")
            c1, c2, _ = st.columns([1, 1, 6])
            for label, decision, col in (("Approve", "APPROVED", c1), ("Reject", "REJECTED", c2)):
                if col.button(label, key=f"{label}_{it['approval_id']}"):
                    r = cached.backend(ctx.backend_url).post("/api/v1/admin/hitl/decide",
                                                             {"approval_id": it["approval_id"], "decision": decision,
                                                              "approver": approver}, ctx.tenant_id)
                    (st.toast if r.ok else st.error)(f"{decision}" if r.ok else r.error)
