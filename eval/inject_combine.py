#!/usr/bin/env python3
"""v0.2: combine regex ∨ trained LR (v0.1) ∨ Prompt-Guard-2. tau_pg is chosen ON TRAIN ONLY (train FPR ≤ 0.5%, maximise recall);
evaluation on the FROZEN test sets happens exactly once and is printed + saved. Subsamples = texts present in the pg cache."""
import hashlib, json
from pathlib import Path
import numpy as np
from eval import inject_bench as B
from eval.inject_train import new_benign

ROOT = Path(__file__).resolve().parent.parent
key = lambda t: hashlib.sha256(t.encode()).hexdigest()[:20]
pg = json.loads((ROOT / "artifacts/inject_pg.json").read_text()); feats = B.get_features(); S = B.load_sets()
z = np.load(ROOT / "artifacts/inject_emb.npz", allow_pickle=True); m = json.loads((ROOT / "src/plugins/injection_lr.json").read_text()); w, b, tau = np.array(m["w"]), m["b"], m["tau"]

def rows(name):
    texts, labels = S[name]; F = feats[name]["f"]; E = z[name]; out = []
    for i, t in enumerate(texts):
        if key(t) in pg:
            x = np.concatenate([E[i], [min(F[i]["rx"], 2.0), F[i]["p"]]]); lr = 1 / (1 + np.exp(-(x @ w + b)))
            out.append({"y": labels[i], "lr": float(lr), "rx": F[i]["rx"] >= 1.0, "pg": pg[key(t)]})
    return out

def nb_rows():   # synthetic in-domain benign used in training of the LR; here only for the pg threshold's FPR on train
    nb = new_benign()[:240]; return [{"y": 0, "lr": 0.0, "rx": False, "pg": pg[key(t)]} for t in nb if key(t) in pg]

def dec(r, t2): return r["rx"] or r["lr"] >= tau or (t2 is not None and r["pg"] >= t2)
def rates(rs, t2):
    pos = [dec(r, t2) for r in rs if r["y"] == 1]; neg = [dec(r, t2) for r in rs if r["y"] == 0]
    return (100 * np.mean(pos) if pos else float("nan"), 100 * np.mean(neg) if neg else float("nan"), len(pos), len(neg))

train = rows("deepset_train") + rows("xtram_train") + nb_rows()
print(f"train rows with pg: {len(train)}"); print("pg-only on train:", {t: tuple(round(x, 2) for x in rates([{**r, 'lr': 0, 'rx': False} for r in train], t)[:2]) for t in (0.5, 0.9, 0.99, 0.999)})
best = None
for t2 in (0.3, 0.5, 0.7, 0.9, 0.99, 0.999, None):
    rec, fpr, *_ = rates(train, t2); print(f"  tau_pg={t2}: TRAIN recall {rec:.1f}% FPR {fpr:.2f}%")
    if fpr <= 0.5 and (best is None or rec > best[1]): best = (t2, rec)
t2 = best[0]; print("chosen tau_pg (train only):", t2)
res = {"tau_pg": t2}
print(f"\nFROZEN TEST (evaluated once): {'set':26s} {'LR+rx recall/FPR':>20s} {'+PG recall/FPR':>18s}   n(pos/neg)")
for name in ("deepset_test", "xtram_test", "ours_holdout_attacks", "ours_benign_indomain"):
    rs = rows(name); a = rates(rs, None); c = rates(rs, t2); res[name] = {"base": a, "combined": c}
    print(f"{'':32s}{name:26s} {a[0]:9.1f} /{a[1]:6.2f}   {c[0]:9.1f} /{c[1]:6.2f}   {c[2]}/{c[3]}")
(ROOT / "artifacts/inject_combine_v02.json").write_text(json.dumps(res, indent=1, default=float))
