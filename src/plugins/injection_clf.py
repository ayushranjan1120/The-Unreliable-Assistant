"""Stage-2 prompt-injection classifier (ONNX, fp32): protectai/deberta-v3-base-prompt-injection-v2.

INT8 dynamic quantization was evaluated and REJECTED: it collapses the attack score (1.00 → 0.06). fp32 only.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "models" / "inj"


class InjectionClassifier:
    """Thread-safe wrapper returning P(injection)."""

    def __init__(self, path: Path = MODEL_DIR, threads: int = 2) -> None:
        self.tok = AutoTokenizer.from_pretrained(str(path))
        so = ort.SessionOptions(); so.intra_op_num_threads, so.inter_op_num_threads = threads, 1
        self.sess = ort.InferenceSession(str(path / "model.onnx"), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self._lock = threading.Lock()

    def p_injection(self, texts: list[str]) -> list[float]:
        """Probability of the INJECTION label for each text (truncated to 256 tokens)."""
        enc = self.tok([t[:2000] for t in texts], padding=True, truncation=True, max_length=256, return_tensors="np")
        feeds = {k: v.astype(np.int64) for k, v in enc.items() if k in self.inputs}
        with self._lock:
            lg = self.sess.run(None, feeds)[0]
        e = np.exp(lg - lg.max(-1, keepdims=True))
        return (e[:, 1] / e.sum(-1)).tolist()
