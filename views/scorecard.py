"""Tab 6 — 36-KPI Scorecard & 50K Load: evidence-standard scorecard, RAGAS gauges, load histogram, span waterfall."""
from __future__ import annotations

import streamlit as st

import cached
import state
from components import cards, charts, tables, trace
from utils import kpi_eval


def render(ctx: state.Ctx) -> None:
    """Render Tab 6."""
    st.subheader("36-KPI Scorecard & 50K Load")
    rows = cached.scorecard(ctx.version, None if ctx.profile == "all" else ctx.profile)
    s = kpi_eval.summarize(rows)
    cards.metric_row([("PASS", s["PASS"], None), ("FAIL", s["FAIL"], None), ("No data", s["NO_DATA"], "no artifact → counts as FAIL in certification"),
                      ("No evidence", s["NO_EVIDENCE"], "value without run id/artifact"), ("Info only", s["INFORMATIONAL"], "dev-cpu, not certifiable"),
                      ("Certified", "YES" if s["certified"] else "NO", f"{ctx.version} · profile {ctx.profile}")])
    if not s["certified"]:
        st.warning("Certification requires all 36 KPIs PASS with run id + artifact, on profile `target` (design §1.15/§1.16).")
    with st.expander("KPI table (36)", expanded=True):
        stages = sorted({r["stage"] for r in rows})
        pick = st.multiselect("Stage filter", stages, default=stages)
        tables.kpi_table([r for r in rows if r["stage"] in pick])
    st.divider()
    a, b, c = st.columns(3)
    by = {r["kpi"]: r for r in rows}
    with a:
        charts.gauge(by["KPI-20"]["value"], "Faithfulness", 0.92)
    with b:
        charts.gauge(by["KPI-21"]["value"], "Answer relevancy", 0.90)
    with c:
        charts.gauge(by["KPI-22"]["value"], "Context entity recall", 0.85)
    st.divider()
    st.markdown("**Latest load result** (k6/JMeter, SLA 200–600 ms)")
    lr = cached.backend_get(ctx.backend_url, "/api/v1/admin/load/latest")
    if lr.ok and lr.data.get("samples_ms"):
        d = lr.data
        st.caption(f"run {d['run_id']} · {d['vus']} VUs · think {d['think_s']} s · profile {d['profile']}"
                   + ("" if d["profile"] == "target" else " · NOT CERTIFIABLE"))
        charts.latency_histogram(d["samples_ms"], d["p50"], d["p95"], d["p99"])
    else:
        cards.no_data("No load-test result recorded.")
    st.divider()
    st.markdown("**Span waterfall (latest trace)**")
    hist = st.session_state["trace_history"]
    trace.span_waterfall(hist[-1].get("spans", []) if hist else [])
