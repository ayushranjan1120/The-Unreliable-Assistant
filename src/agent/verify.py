"""Verification subagent (independent context): claim→chunk NLI veto + deterministic numeric/entity grounding.

Two INDEPENDENT checks: (1) NLI entailment of each claim by the retrieved context (the veto used at serving time);
(2) numeric/entity grounding — every number/id in the answer must occur in the context (used to MEASURE KPI-24 so the
veto does not grade itself).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from src import telemetry
from src.rag.onnx_models import OnnxCrossEncoder

NLI_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "models" / "nli"
_SENT = re.compile(r"(?<=[.!?])\s+")
_FACT = re.compile(r"\b(?:SKU-\d{3}|LOT-\d{5}|[a-z]+-\d{2}|\d+(?:\.\d+)?)\b", re.I)


def claims(answer: str) -> list[str]:
    """Split an answer into atomic-ish claims (sentences)."""
    return [s.strip() for s in _SENT.split(answer) if len(s.strip()) > 3]


def ungrounded_facts(answer: str, context: str) -> list[str]:
    """Numbers/ids in ``answer`` that are not in ``context`` (deterministic, model-free). A number is also accepted when it is the exact
    sum or absolute difference of two numbers in the context (derived comparison/arithmetic answers); wrong arithmetic is still flagged."""
    ctx = context.lower()
    ctx_nums = sorted({float(n) for n in re.findall(r"(?<![\w.-])\d+(?:\.\d+)?", ctx)})[:60]
    derived = {round(abs(a - b), 6) for a in ctx_nums for b in ctx_nums} | {round(a + b, 6) for a in ctx_nums for b in ctx_nums}
    out = []
    for f in dict.fromkeys(_FACT.findall(answer)):
        if f.lower() in ctx:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", f) and round(float(f), 6) in derived:
            continue
        out.append(f)
    return out


class NliVerifier:
    """Sentence-level entailment with cross-encoder/nli-deberta-v3-small (ONNX)."""

    def __init__(self, path: Path = NLI_DIR) -> None:
        self.m = OnnxCrossEncoder(path, 512, threads=2)
        import json
        cfg = json.loads((path / "config.json").read_text())
        self.ent = [int(i) for i, l in cfg["id2label"].items() if l.lower().startswith("entail")][0]

    def entail(self, premise: str, hyps: list[str]) -> list[float]:
        """P(entailment) per hypothesis."""
        if not hyps:
            return []
        lg = self.m.logits([(premise, h) for h in hyps])
        e = np.exp(lg - lg.max(-1, keepdims=True))
        return (e[:, self.ent] / e.sum(-1)).tolist()

    @staticmethod
    def _windows(chunk: str) -> list[str]:
        """Sentence and 2-sentence windows of a chunk (small NLI models degrade on long multi-fact premises)."""
        sents = [x.strip() for x in _SENT.split(chunk) if x.strip()]
        return sents + [f"{a} {b}" for a, b in zip(sents, sents[1:])]

    def verify(self, answer: str, chunk_texts: list[str], min_entail: float = 0.5) -> dict:
        """Claim→chunk check. A claim is supported if (1) it is a verbatim quote of a chunk, else (2) some sentence window of some
        chunk entails it, else (3) the joined context entails it (multi-chunk claims). Numeric/entity grounding failure also vetoes."""
        with telemetry.span("verify.nli"):
            cl = claims(answer)
            ctx = " ".join(chunk_texts)
            norm = lambda t: re.sub(r"\s+", " ", t.lower()).strip()
            nctx = [norm(c) for c in chunk_texts]
            strip = lambda t: re.sub(r"[^a-z0-9 ]", " ", norm(t)).split()
            ctxw = set(w for nc in nctx for w in strip(nc))
            def span_ok(c: str) -> bool:      # short span answers ("42 months.", "Medical Devices"): every token must occur in the context
                w = strip(c); return 0 < len(w) <= 4 and all(x in ctxw for x in w)
            joined = " ".join(nctx)
            def multi_entity_ok(c: str) -> bool:   # comparison claims span chunks (small NLI cannot): accept if all ids+numbers are grounded (KNOWN LIMITATION: semantic reversal passes)
                ids = set(re.findall(r"sku-\d{3}|sup-\d{2}", c.lower()))
                return len(ids) >= 2 and all(i in joined for i in ids) and not ungrounded_facts(c, joined)
            todo = [c for c in cl if not any(norm(c) in nc for nc in nctx) and not span_ok(c) and not multi_entity_ok(c)]   # (1) quotes / grounded spans need no NLI
            bad: list[str] = []
            if todo:
                wins = [w for ch in chunk_texts for w in self._windows(ch)][:48]
                pairs = [(w, c) for c in todo for w in wins]
                lg = self.m.logits(pairs)
                e = np.exp(lg - lg.max(-1, keepdims=True)); pe = (e[:, self.ent] / e.sum(-1)).reshape(len(todo), len(wins))
                weak = [c for c, row in zip(todo, pe) if row.max() < min_entail]       # (2)
                if weak:
                    for c, p in zip(weak, self.entail(ctx, weak)):                       # (3)
                        if p < min_entail:
                            bad.append(c)
            ug = ungrounded_facts(answer, ctx)
            return {"supported_ratio": 1.0 if not cl else 1 - len(bad) / len(cl), "unsupported": bad, "ungrounded_facts": ug,
                    "vetoed": bool(bad or ug), "quoted": len(cl) - len(todo), "nli_checked": len(todo)}
