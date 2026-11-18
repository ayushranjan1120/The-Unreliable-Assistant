#!/usr/bin/env python3
"""ADR-7 generation bake-off (design §1.4.1): identical retrieval for every candidate; the real runtime is restarted per model.
Per candidate: golden subset (stratified) + unanswerable questions FORCED through generation. Prints the accuracy-parity table
vs the large reference model. TTFT here includes internet RTT to the provider → informational only (not the target vLLM number)."""
import json, os, subprocess, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
CANDS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["openai/gpt-oss-20b", "allam-2-7b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
LIMIT = sys.argv[2] if len(sys.argv) > 2 else "80"
out = {}
for m in CANDS:
    env = {**os.environ, "LLM_BACKEND": "routed", "GROQ_MODEL": m, "OPENROUTER_API_KEY": ""}      # Groq only: no silent OpenRouter hop
    subprocess.run([str(ROOT / "scripts/dev_stack.sh"), "restart"], env=env, cwd=ROOT, capture_output=True)
    subprocess.run(["redis-cli", "flushdb"], capture_output=True)
    f = ROOT / "artifacts" / f"bakeoff_{m.replace('/', '_')}.json"
    t = time.time()
    r = subprocess.run([sys.executable, "-m", "eval.golden_eval", "--limit", LIMIT, "--no-record", "--force-negatives", "--save", str(f)], env=env, cwd=ROOT, capture_output=True, text=True)
    if not f.exists():
        print(m, "FAILED:", r.stderr[-300:]); continue
    out[m] = json.loads(f.read_text()); print(f"{m}: done in {time.time()-t:.0f}s", flush=True)
subprocess.run([str(ROOT / "scripts/dev_stack.sh"), "restart"], env={**os.environ, "LLM_BACKEND": "extractive"}, cwd=ROOT, capture_output=True)
ref = out.get("openai/gpt-oss-120b")
print(f"\n{'model':24s} {'rel.proxy':>9s} {'entRec':>7s} {'abst%':>6s} {'negAns':>6s} {'halluc%':>7s} {'veto%':>6s} {'ttftP95':>8s} backends")
for m, d in out.items():
    print(f"{m:24s} {d['answer_relevancy_proxy']:9.3f} {d['context_entity_recall']:7.3f} {d['abstention_accuracy_pct']:6.1f} {d['neg_answered']:6d} {d['hallucination_rate_pct']:7.2f} {d['veto_first_pct']:6.1f} {str((d['ttft_ms'] or {}).get('p95')):>8s} {d['llm_backends']}")
print("\nper-stratum relevancy proxy:")
for m, d in out.items():
    print(f"  {m:24s}", {k: round(v['relevancy'], 3) for k, v in d["per_stratum"].items()})
Path(ROOT / "artifacts" / "bakeoff_summary.json").write_text(json.dumps({m: {k: v for k, v in d.items() if k != "rows"} for m, d in out.items()}, indent=1))
