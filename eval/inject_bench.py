#!/usr/bin/env python3
"""v0.1 injection generalisation benchmark (FN-002). Features are computed once and cached; policies are compared offline.
TUNING uses only: public TRAIN splits (+ our in-domain benign set for the false-positive constraint).
EVALUATION uses the FROZEN public TEST splits (never tuned on) and our own authored holdout.
Caveat: protectai/deberta-v3-base-prompt-injection-v2 may have seen these public datasets in pretraining → public-test numbers are optimistic."""
from __future__ import annotations

import glob, json, sys, time
from pathlib import Path

import numpy as np
import pandas as pd

from src.agent import grounding as G
from src.config import get_settings
from src.graphify import GraphIndex
from src.harness.repair import normalize_entities
from src.plugins.injection import scan
from src.plugins.injection_clf import InjectionClassifier
from src.plugins.pii import redact

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "artifacts" / "inject_features.json"


def load_sets() -> dict[str, tuple[list[str], list[int]]]:
    fr = ROOT / "eval/data/frozen"
    rd = lambda pat: pd.read_parquet(glob.glob(str(fr / pat))[0])
    sets = {}
    for name, pat in (("deepset_train", "deepset*train*"), ("deepset_test", "deepset*test*"), ("xtram_train", "xTRam1*train*"), ("xtram_test", "xTRam1*test*")):
        df = rd(pat)
        if name == "xtram_train":                                   # subsample for CPU time (seeded, stratified)
            df = pd.concat([df[df["label"] == k].sample(min(int((df["label"] == k).sum()), 700), random_state=1) for k in (0, 1)])
        sets[name] = (df["text"].tolist(), df["label"].astype(int).tolist())
    ben = [json.loads(l) for l in (ROOT / "eval/data/benign_control.jsonl").read_text().splitlines()]
    sets["ours_benign_indomain"] = ([b["query"] for b in ben if b.get("in_domain", True)], [0] * sum(b.get("in_domain", True) for b in ben))
    from eval import redteam as rt
    atk = rt.H_DIRECT + rt.H_FUZZ + rt.H_INDIRECT
    sets["ours_holdout_attacks"] = (atk, [1] * len(atk))
    return sets


def features(texts: list[str], clf: InjectionClassifier, graph: GraphIndex, vocab) -> list[dict]:
    out, probs = [], []
    for i in range(0, len(texts), 16):
        probs += clf.p_injection([t[:1200] for t in texts[i:i + 16]])
    for t, p in zip(texts, probs):
        red = redact(t)[0]; v = scan(red)
        out.append({"p": p, "rx": v.score, "hits": v.hits, "oov": G.coverage_ratio(t, vocab), "ent": bool(normalize_entities(t)), "len": len(t)})
    return out


def get_features() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    s = get_settings(); graph = GraphIndex(s.vault_dir, s.graph_path); vocab = G.vault_vocab(graph); clf = InjectionClassifier()
    sets, feats = load_sets(), {}
    for name, (texts, labels) in sets.items():
        t0 = time.time(); feats[name] = {"labels": labels, "f": features(texts, clf, graph, vocab)}; print(f"  {name}: {len(texts)} texts in {time.time()-t0:.0f}s", flush=True)
    CACHE.write_text(json.dumps(feats)); return feats


# ---- policies: each maps a feature row -> blocked? ---------------------------------------------------------------------
def p_regex(f, **_): return f["rx"] >= 1.0
def p_clf(f, tau=0.99, **_): return f["p"] >= tau
def p_current(f, tau=0.99, **_): return p_regex(f) or (f["p"] >= tau and not (f["ent"] and not f["hits"]))     # v0 shield
def p_combined(f, tau=0.99, **_): return p_regex(f) or f["p"] >= tau                                         # no entity allowance
def p_fast(f, tau=0.99, oov_skip=0.2, **_):                                                                  # skip classifier for near-pure in-domain text
    if p_regex(f): return True
    if f["oov"] <= oov_skip and f["ent"]: return False
    return f["p"] >= tau
POLICIES = {"regex_only": p_regex, "clf_only@0.99": p_clf, "v0_current": p_current, "regex|clf (no allowance)": p_combined, "v0.1_fastpath": p_fast}


def rates(fs, labels, pol, **kw):
    blocked = [pol(f, **kw) for f in fs]
    pos = [b for b, l in zip(blocked, labels) if l == 1]; neg = [b for b, l in zip(blocked, labels) if l == 0]
    return (100 * sum(pos) / len(pos) if pos else None, 100 * sum(neg) / len(neg) if neg else None, len(pos), len(neg))


def main() -> dict:
    feats = get_features()
    print(f"\n{'policy':26s} " + " | ".join(f"{n:22s}" for n in ("deepset_test", "xtram_test", "ours_holdout", "ours_benign")))
    print(f"{'':26s} " + " | ".join(f"{'recall%   fpr%':22s}" for _ in range(4)))
    res = {}
    for pn, pol in POLICIES.items():
        row = []
        for sn in ("deepset_test", "xtram_test", "ours_holdout_attacks", "ours_benign_indomain"):
            r = rates(feats[sn]["f"], feats[sn]["labels"], pol); res[(pn, sn)] = r
            row.append(f"{r[0]:6.1f}      -  " if r[0] is not None and r[1] is None else (f"    -     {r[1]:6.2f}" if r[0] is None else f"{r[0]:6.1f}  {r[1]:6.2f}"))
        print(f"{pn:26s} " + " | ".join(f"{x:22s}" for x in row))
    # threshold sweep for the classifier, picked on TRAIN only
    tr = [(f, l) for sn in ("deepset_train", "xtram_train") for f, l in zip(feats[sn]["f"], feats[sn]["labels"])] 
    benign_ours = feats["ours_benign_indomain"]["f"]
    print("\nthreshold sweep (TRAIN splits + our in-domain benign for FPR):")
    for tau in (0.5, 0.8, 0.95, 0.99, 0.999, 0.9999):
        rec = 100 * sum(p_combined(f, tau=tau) for f, l in tr if l == 1) / sum(l == 1 for _, l in tr)
        fpr = 100 * sum(p_combined(f, tau=tau) for f, l in tr if l == 0) / sum(l == 0 for _, l in tr)
        fpr_o = 100 * sum(p_fast(f, tau=tau) for f in benign_ours) / len(benign_ours)
        print(f"  tau={tau:<7} train recall {rec:5.1f}%  train FPR {fpr:5.2f}%  our in-domain benign FPR (fastpath) {fpr_o:5.2f}%")
    return res


if __name__ == "__main__":
    main()
