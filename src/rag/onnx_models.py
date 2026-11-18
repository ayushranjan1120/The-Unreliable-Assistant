"""ONNX Runtime wrappers for the bi-encoder, cross-encoder and NLI model (design ADR-2).

dev-cpu uses small models on CPUExecutionProvider; ``target`` uses FP16 on CUDAExecutionProvider via the same API.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _providers() -> list[str]:
    av = ort.get_available_providers()
    return [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in av]


class _Onnx:
    def __init__(self, path: Path, max_len: int, threads: int = 2) -> None:
        self.tok = AutoTokenizer.from_pretrained(str(path))
        so = ort.SessionOptions()
        so.intra_op_num_threads, so.inter_op_num_threads = threads, 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(path / "model.onnx"), so, providers=_providers())
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.max_len = max_len

    def _run(self, enc: dict) -> np.ndarray:
        feeds = {k: v.astype(np.int64) for k, v in enc.items() if k in self.inputs}
        return self.sess.run(None, feeds)[0]


class OnnxEncoder(_Onnx):
    """Bi-encoder: CLS pooling + L2 normalization (BGE convention)."""

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        """Return normalized float32 embeddings ``(n, dim)``."""
        if is_query:
            texts = [BGE_QUERY_PREFIX + t for t in texts]
        enc = self.tok(texts, padding=True, truncation=True, max_length=self.max_len, return_tensors="np")
        out = self._run(dict(enc))
        v = out[:, 0].astype(np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


class OnnxCrossEncoder(_Onnx):
    """Cross-encoder over ``(query, passage)`` pairs; ``logits`` are model-native, ``score`` is sigmoid."""

    def logits(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Raw logits ``(n, num_labels)``."""
        a, b = [p[0] for p in pairs], [p[1] for p in pairs]
        enc = self.tok(a, b, padding=True, truncation="only_second", max_length=self.max_len, return_tensors="np")
        return self._run(dict(enc)).astype(np.float32)

    def score(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Sigmoid relevance in (0, 1) for single-logit rerankers."""
        z = self.logits(pairs)[:, 0]
        return 1.0 / (1.0 + np.exp(-z))
