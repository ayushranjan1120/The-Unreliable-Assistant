#!/usr/bin/env python3
"""Capture real screenshots of the Streamlit workbench (needs the stack + UI running). Output: docs/screenshots/*.png"""
import sys
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"; OUT = "docs/screenshots"
def settle(pg, ms=2500): pg.wait_for_timeout(ms)
def tab(pg, i): pg.locator('[role="tab"]').nth(i).click(); settle(pg, 3500)
def full(pg, name): pg.screenshot(path=f"{OUT}/{name}.png", full_page=False)

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1150})
    pg.goto(URL, wait_until="networkidle"); pg.wait_for_selector("text=Workbench", timeout=30000); settle(pg, 4000)
    # Tab 1: route a sample (multi-hop) and show answer + ReAct trace
    sb = pg.locator('[data-testid="stMain"] [data-testid="stSelectbox"]').first; sb.scroll_into_view_if_needed(); sb.click(); settle(pg, 1500)
    pg.locator('[role="option"]', has_text="Multi-hop").first.click(); settle(pg, 1500)
    pg.get_by_role("button", name="Route query").click(); pg.wait_for_selector("text=Route", timeout=90000); settle(pg, 3000)
    pg.evaluate("window.scrollTo(0, 900)"); pg.mouse.wheel(0, 900); settle(pg, 1500); full(pg, "01_architecture_triage")
    pg.mouse.wheel(0, -2000); full(pg, "01_architecture_top")
    # Tab 2: RAG lab
    tab(pg, 1); pg.get_by_role("button", name="Run retrieval").click(); settle(pg, 9000); full(pg, "02_rag_lab")
    # Tab 3: MCP & plugins
    tab(pg, 2); settle(pg, 3000); full(pg, "03_mcp_plugins")
    # Tab 4: memory & graphify
    tab(pg, 3); settle(pg, 4000); full(pg, "04_memory_graphify")
    # Tab 5: red team
    tab(pg, 4); pg.get_by_role("button", name="Run selected").click(); pg.wait_for_selector("text=Executions", timeout=420000); settle(pg, 2000); full(pg, "05_redteam_breaker")
    # Tab 6: scorecard
    tab(pg, 5); settle(pg, 4000); full(pg, "06_kpi_scorecard")
    pg.mouse.wheel(0, 700); settle(pg, 1500); full(pg, "06_kpi_scorecard_table")
    b.close(); print("screenshots written to", OUT)
