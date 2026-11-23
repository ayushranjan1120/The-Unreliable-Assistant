#!/usr/bin/env python3
"""v0.1 guardrail evidence → KPI-01 (latency), 02 (PII), 03 (injection recall on FROZEN public test), 06 (in-domain benign FPR through the REAL shield).
Seed-prompt red-team numbers are deliberately NOT used as evidence (tuned-on)."""
import json, time, uuid
from pathlib import Path
import numpy as np
from eval import inject_bench as B, redteam as rt
from src import telemetry
from src.config import get_settings
from src.errors import ToolError
from src.plugins.hooks import PluginCtx, PluginMeta, PluginRegistry
from src.plugins.pii import PiiRedactor, redact
from utils import results_store

ROOT = Path(__file__).resolve().parent.parent
s = get_settings(); rid = results_store.new_run_id("guard01")
shield = rt.make_shield(); reg = PluginRegistry()
reg.register(PiiRedactor(), PluginMeta("pii_redactor", "PRE_EXECUTION", 10, True)); reg.register(shield, PluginMeta("injection_shield", "PRE_EXECUTION", 20, True))
sets = B.load_sets(); feats = B.get_features(); m = json.loads((ROOT / "src/plugins/injection_lr.json").read_text()); w = np.array(m["w"]); b = m["b"]
z = np.load(ROOT / "artifacts/inject_emb.npz", allow_pickle=True)

def real(texts):                                   # decisions from the REAL registry path
    out = []
    for t in texts:
        try: reg.run_pre(PluginCtx("tenant_1", "user_1", str(uuid.uuid4()), "a" * 32), t); out.append(False)
        except ToolError: out.append(True)
    return out

telemetry.METRICS.reset(); benign = sets["ours_benign_indomain"][0]
blk_b = real(benign); fpr = 100 * sum(blk_b) / len(benign); p95 = telemetry.METRICS.percentile("hook.pre", 95)
hold = sets["ours_holdout_attacks"][0]; rec_h = 100 * sum(real(hold)) / len(hold)
f = feats["xtram_test"]; lab = np.array(f["labels"]); X = np.hstack([z["xtram_test"], np.array([[min(x["rx"], 2.0), x["p"]] for x in f["f"]])])
blk = (1 / (1 + np.exp(-(X @ w + b))) >= m["tau"]) | np.array([x["rx"] >= 1 for x in f["f"]])
rec_x, fpr_x = 100 * blk[lab == 1].mean(), 100 * blk[lab == 0].mean()
pii = rt.pii_bench()
out = {"hook_pre_p95_ms": p95, "benign_indomain_fpr_pct": fpr, "benign_n": len(benign), "ours_holdout_recall_pct": rec_h, "ours_holdout_n": len(hold), "xtram_test_recall_pct": rec_x, "xtram_test_n_attacks": int((lab == 1).sum()),
       "xtram_test_public_benign_fpr_pct": fpr_x, "pii_recall_pct": pii["recall_pct"], "pii_n": pii["n"], "note": "public sets may overlap the classifier's pretraining (optimistic)"}
art = ROOT / "artifacts" / f"guard_bench_v01_{rid}.json"; art.write_text(json.dumps(out, indent=1))
rec = lambda k, v, n, **ex: results_store.record_run(k, v, n=n, run_id=rid, version=s.version, rung="L0", profile=s.profile, artifact=str(art.relative_to(ROOT)), extra=ex or None)
rec(1, p95, len(benign)); rec(2, pii["recall_pct"], pii["n"]); rec(3, rec_x, out["xtram_test_n_attacks"], note=f"frozen public test; own holdout recall {rec_h:.1f}% (n={len(hold)})"); rec(6, fpr, len(benign))
print(json.dumps(out, indent=1))
