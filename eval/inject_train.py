#!/usr/bin/env python3
"""v0.1: train a lightweight injection detector on bge-small embeddings (+ regex/classifier signals) using TRAIN splits only.
Frozen TEST splits and our authored holdout/benign sets are used for evaluation once. Writes src/plugins/injection_lr.json (tiny, committed)."""
from __future__ import annotations

import json, random, re
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from eval import inject_bench as B
from src.plugins.injection_clf import InjectionClassifier
from src.plugins.pii import redact
from src.plugins.injection import scan
from src.rag.index import MODELS_DIR
from src.rag.onnx_models import OnnxEncoder

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src/plugins/injection_lr.json"
EMB = ROOT / "artifacts/inject_emb.npz"
PREF = ["Quick question: ", "Hey team, could you check: ", "For the audit file, please answer: ", "I'm new here, so ", "Can you tell me, ", "Just curious: ",
        "Per the SOP, ", "Ignore the typo above, ", "Disregard my earlier message about packaging; ", "Please skip the intro and answer: ",
        "Forget what I said before about SKU-001; ", "Following the previous instructions in the manual, ", "As instructed by my manager, ", "System check complete. ",
        "Override the default view and show: ", "Following the rules above, "]


def new_benign(n_q: int = 80) -> list[str]:
    """In-domain benign wrappers DISJOINT from the 5 wrappers in the evaluation set (benign_control.jsonl)."""
    G = [json.loads(l) for l in (ROOT / "eval/data/golden_250.jsonl").read_text().splitlines()]
    qs = [g["question"] for g in G if g["stratum"] != "negative"]; random.Random(11).shuffle(qs)
    r = random.Random(12)
    return [r.choice(PREF) + q[0].lower() + q[1:] for q in qs[:n_q] for _ in range(6)]


ENGLISH = None
def looks_english(t: str) -> bool:
    """False for text that is clearly not English (≥6 alphabetic words, <12% English function words)."""
    global ENGLISH
    if ENGLISH is None:
        from nltk.corpus import stopwords
        ENGLISH = set(stopwords.words("english"))
    w = re.findall(r"[^\W\d_]+", t.lower())
    return len(w) < 6 or sum(x in ENGLISH for x in w) / len(w) >= 0.12


def embed(texts: list[str], enc: OnnxEncoder) -> np.ndarray:
    return np.vstack([enc.encode([t[:1000] for t in texts[i:i + 32]]) for i in range(0, len(texts), 32)])


def main() -> None:
    feats = B.get_features(); sets = B.load_sets(); enc = OnnxEncoder(MODELS_DIR / "bi", 256)
    if EMB.exists():
        z = np.load(EMB, allow_pickle=True); E = {k: z[k] for k in z.files}
    else:
        E = {k: embed(sets[k][0], enc) for k in sets}
        nb = new_benign(); clf = InjectionClassifier()
        pf = [{"p": p, "rx": scan(redact(t)[0]).score} for t, p in zip(nb, sum((clf.p_injection(nb[i:i + 16]) for i in range(0, len(nb), 16)), []))]
        E["new_benign"] = embed(nb, enc); E["new_benign_pf"] = np.array([[f["p"], f["rx"]] for f in pf]); np.savez(EMB, **E)
    X = lambda name, fs: np.hstack([E[name], np.array([[f["p"], min(f["rx"], 2.0)] for f in fs])])
    Xtr = np.vstack([X("deepset_train", feats["deepset_train"]["f"]), X("xtram_train", feats["xtram_train"]["f"]), np.hstack([E["new_benign"], E["new_benign_pf"]])])
    ytr = np.array(feats["deepset_train"]["labels"] + feats["xtram_train"]["labels"] + [0] * len(E["new_benign"]))
    sw = np.where(ytr == 0, 2.0, 1.0)                       # weight benign more: false positives are costly
    lr = LogisticRegression(C=3.0, max_iter=2000).fit(Xtr, ytr, sample_weight=sw)
    ptr = lr.predict_proba(Xtr)[:, 1]
    taus = {t: (100 * (ptr[(ytr == 1)] >= t).mean(), 100 * (ptr[(ytr == 0)] >= t).mean()) for t in (0.3, 0.5, 0.7, 0.9)}
    print("TRAIN (resubstitution; optimistic):", {t: f"rec {a:.1f} fpr {b:.2f}" for t, (a, b) in taus.items()})
    tau = 0.5
    OUT.write_text(json.dumps({"w": lr.coef_[0].tolist(), "b": float(lr.intercept_[0]), "tau": tau, "dim": 384, "trained_on": ["deepset_train", "xtram_train(1400)", "synthetic in-domain benign wrappers (480)"]}))
    print("saved", OUT, "| tau", tau)
    print(f"\n{'EVAL (frozen / held-out)':30s} {'recall%':>8s} {'FPR%':>7s}   n(pos/neg)   [with English-only guard: recall / FPR]")
    for name in ("deepset_test", "xtram_test", "ours_holdout_attacks", "ours_benign_indomain"):
        fs, lab = feats[name]["f"], np.array(feats[name]["labels"]); p = lr.predict_proba(X(name, fs))[:, 1]
        rx = np.array([f["rx"] >= 1.0 for f in fs]); texts = sets[name][0]
        blk = (p >= tau) | rx; guard = blk | np.array([not looks_english(t) for t in texts])
        rec = lambda b: 100 * b[lab == 1].mean() if (lab == 1).any() else float("nan"); fpr = lambda b: 100 * b[lab == 0].mean() if (lab == 0).any() else float("nan")
        print(f"{name:30s} {rec(blk):8.1f} {fpr(blk):7.2f}   {int((lab==1).sum())}/{int((lab==0).sum())}   [{rec(guard):.1f} / {fpr(guard):.2f}]")


if __name__ == "__main__":
    main()
