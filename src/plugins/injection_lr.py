"""Trained injection detector (v0.1): logistic regression over [bge-small embedding, regex score, DeBERTa P(injection)].
Weights live in src/plugins/injection_lr.json (trained by eval/inject_train.py on PUBLIC TRAIN splits only)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

WEIGHTS = Path(__file__).resolve().parent / "injection_lr.json"


class InjectionDetector:
    """prob(text, regex_score, clf_p) → P(injection)."""

    def __init__(self, encoder, path: Path = WEIGHTS) -> None:
        m = json.loads(path.read_text())
        self.w, self.b, self.tau, self.enc = np.asarray(m["w"], dtype=np.float32), float(m["b"]), float(m["tau"]), encoder

    def prob(self, text: str, rx: float, p_clf: float) -> float:
        e = self.enc.encode([text[:1000]])[0]
        z = float(np.dot(self.w[:-2], e) + self.w[-2] * min(rx, 2.0) + self.w[-1] * p_clf + self.b)
        return 1.0 / (1.0 + np.exp(-z))
