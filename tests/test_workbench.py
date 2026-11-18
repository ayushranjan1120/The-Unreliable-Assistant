"""L0 workbench acceptance (design §1.13.1): utils never import streamlit; app boots with backend down."""
import ast
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def test_utils_do_not_import_streamlit():
    for f in (ROOT / "utils").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.split(".")[0] == "streamlit" for n in names), f"{f.name} imports streamlit"


def test_app_boots_with_backend_down(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:9")  # nothing listens
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6
    assert any("offline" in w.value for w in at.sidebar.warning)


def test_scorecard_never_passes_without_data(monkeypatch, tmp_path):
    from utils import kpi_eval
    rows = kpi_eval.scorecard({})
    assert len(rows) == 36 and all(r["status"] == "NO_DATA" for r in rows)
    assert not kpi_eval.summarize(rows)["certified"]


def test_evidence_standard_and_profile_rules():
    from utils import kpi_eval
    spec = kpi_eval.SPEC_BY_ID[32]
    ok = {"value": 400, "n": 1000, "run_id": "r1", "artifact": "a.json", "profile": "target"}
    assert kpi_eval.evaluate_kpi(spec, ok)["status"] == "PASS"
    assert kpi_eval.evaluate_kpi(spec, {**ok, "profile": "dev-cpu"})["status"] == "INFORMATIONAL"
    assert kpi_eval.evaluate_kpi(spec, {**ok, "artifact": None})["status"] == "NO_EVIDENCE"
    assert kpi_eval.evaluate_kpi(spec, {**ok, "value": 451})["status"] == "FAIL"


def test_workbench_renders_with_live_backend(monkeypatch):
    """Skipped unless the stack is up on :8000; asserts the six tabs render real data without exceptions."""
    import httpx, pytest
    try:
        httpx.get("http://127.0.0.1:8000/healthz", timeout=2).raise_for_status()
    except Exception:
        pytest.skip("backend not running")
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:8000")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6 and not any("View failed" in e.value for e in at.error)
    assert any("online" in s.value for s in at.sidebar.success)
