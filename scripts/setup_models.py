#!/usr/bin/env python3
"""One-shot, idempotent setup for a fresh clone: export the ONNX models (dev-cpu profile), build the Graphify graph, build the retrieval
index and sign the MCP manifests. Needs network access to huggingface.co the first time (~1.7 GB on disk, ≈5-8 min on 4 vCPU).
Usage: python -m scripts.setup_models [--force]"""
import json, subprocess, sys
from pathlib import Path
from src.config import MANIFESTS, ROOT, get_settings

force = "--force" in sys.argv
OUT = ROOT / "artifacts" / "models"
JOBS = [("bi", "BAAI/bge-small-en-v1.5", "feat"), ("rerank", "cross-encoder/ms-marco-MiniLM-L-6-v2", "cls"),
        ("nli", "cross-encoder/nli-deberta-v3-small", "cls"), ("inj", "protectai/deberta-v3-base-prompt-injection-v2", "cls")]

def export() -> None:
    from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTModelForSequenceClassification
    from transformers import AutoTokenizer
    for sub, name, kind in JOBS:
        d = OUT / sub
        if (d / "model.onnx").exists() and not force:
            print(f"  skip {sub} (exists)"); continue
        print(f"  exporting {name} -> {d}")
        cls = ORTModelForFeatureExtraction if kind == "feat" else ORTModelForSequenceClassification
        m = cls.from_pretrained(name, export=True); m.save_pretrained(d); AutoTokenizer.from_pretrained(name).save_pretrained(d)

def main() -> None:
    s = get_settings()
    print("1/4 ONNX models"); export()
    if not (ROOT / "eval/data/golden_250.jsonl").exists() or not any((ROOT / "knowledge_vault").glob("*.md")):
        print("   generating synthetic corpus"); subprocess.run([sys.executable, "eval/make_corpus.py"], check=True, cwd=ROOT)
    print("2/4 Graphify graph")
    subprocess.run([sys.executable, "-m", "mcp_servers.graphify_build", "./src", "./knowledge_vault", "--output", "artifacts/graph.json"], check=True, cwd=ROOT)
    print("3/4 retrieval index")
    from src.rag.index import build_index
    if force or not (s.index_dir / "manifest.json").exists():
        g = json.loads((ROOT / "artifacts/graph.json").read_text())["graph_hash"]; m = build_index(s, graph_hash=g); print("   chunks:", m["n_chunks"])
    else:
        print("   skip (index exists; --force to rebuild)")
    print("4/4 sign MCP manifests"); subprocess.run([sys.executable, "-m", "mcp_servers.manifest", s.version], check=True, cwd=ROOT)
    print("done. Next: scripts/dev_stack.sh start && scripts/dev_stack.sh ui")

main()
