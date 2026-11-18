"""Offline index build + manifest (design §1.3 rule 5: read-only, versioned, atomically swapped)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import faiss
import numpy as np

from src.config import Settings
from src.rag.bm25 import tokenize
from src.rag.onnx_models import OnnxEncoder
from src.vault import load_vault, vault_hash

CHUNK_TOKENS, OVERLAP = 512, 64
_WS = re.compile(r"\S+")
MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "models"


def chunk_text(text: str, size: int = CHUNK_TOKENS, overlap: int = OVERLAP) -> list[str]:
    """Split on whitespace pieces into windows of ``size`` tokens (≈1.3 pieces/token) with ``overlap``; atomic notes stay whole."""
    words = _WS.findall(text)
    w, o = int(size / 1.3), int(overlap / 1.3)
    if len(words) <= w:
        return [text.strip()]
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + w]))
        if i + w >= len(words):
            break
        i += w - o
    return out


def build_index(s: Settings, graph_hash: str = "") -> dict:
    """Build chunks, embeddings, FAISS HNSW and manifest into a fresh dir, then swap atomically."""
    notes = load_vault(s.vault_dir)
    chunks, seen = [], set()
    for n in notes.values():
        for j, part in enumerate(chunk_text(n.body)):
            h = hashlib.sha256(part.encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            chunks.append({"chunk_id": h[:16], "chunk_hash": h, "source_id": n.id, "part": j, "text": part, "domain": n.domain,
                           "risk_tier": n.tier, "tenant": "shared", "tokens": tokenize(part)})
    enc = OnnxEncoder(MODELS_DIR / "bi", 512)
    embs = np.vstack([enc.encode([c["text"] for c in chunks[i:i + 32]]) for i in range(0, len(chunks), 32)])
    idx = faiss.IndexHNSWFlat(embs.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
    idx.hnsw.efConstruction, idx.hnsw.efSearch = 200, s.efsearch
    idx.add(embs)
    man = {"version": s.version, "profile": s.profile, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "n_chunks": len(chunks),
           "vault_hash": vault_hash(s.vault_dir), "graph_hash": graph_hash, "bi_encoder": s.manifest.bi_encoder,
           "reranker": s.manifest.reranker, "rerank_threshold": s.manifest.rerank_threshold, "llm_backend": s.manifest.llm_backend,
           "llm_model": s.manifest.llm_model, "faiss": {"type": "HNSW", "M": 32, "efConstruction": 200, "efSearch": s.efsearch},
           "bm25": {"k1": s.bm25_k1, "b": s.bm25_b}, "chunking": {"tokens": CHUNK_TOKENS, "overlap": OVERLAP}}
    tmp = s.index_dir.with_suffix(".tmp")
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    (tmp / "chunks.json").write_text(json.dumps(chunks))
    np.save(tmp / "embeddings.npy", embs)
    faiss.write_index(idx, str(tmp / "faiss.index"))
    (tmp / "manifest.json").write_text(json.dumps(man, indent=1))
    prev = s.index_dir.with_suffix(".prev")                     # keep N-1 for rollback (design §1.11)
    shutil.rmtree(prev, ignore_errors=True)
    if s.index_dir.exists():
        s.index_dir.rename(prev)
    tmp.rename(s.index_dir)
    return man


def load_index(index_dir: Path) -> tuple[list[dict], np.ndarray, faiss.Index, dict]:
    """Load chunks, embeddings, FAISS index, manifest."""
    chunks = json.loads((index_dir / "chunks.json").read_text())
    return (chunks, np.load(index_dir / "embeddings.npy"), faiss.read_index(str(index_dir / "faiss.index")),
            json.loads((index_dir / "manifest.json").read_text()))
