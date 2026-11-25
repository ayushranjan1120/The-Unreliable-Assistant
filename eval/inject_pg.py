#!/usr/bin/env python3
"""Score every injection evaluation text with Meta Prompt-Guard-2-86M (Groq-hosted) as a 2nd INDEPENDENT detector; cache by text hash.
Texts leave the host: only synthetic/public data is sent (never real tenant data)."""
import asyncio, hashlib, json, os
from pathlib import Path
import httpx
from eval import inject_bench as B
from eval.inject_train import new_benign
from src.config import get_settings

ROOT = Path(__file__).resolve().parent.parent; CACHE = ROOT / "artifacts/inject_pg.json"; MODEL = "meta-llama/llama-prompt-guard-2-86m"
get_settings(); H = {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}
key = lambda t: hashlib.sha256(t.encode()).hexdigest()[:20]

async def score(c: httpx.AsyncClient, t: str, sem) -> float | None:
    async with sem:
        for a in range(6):
            r = await c.post("https://api.groq.com/openai/v1/chat/completions", headers=H, json={"model": MODEL, "messages": [{"role": "user", "content": t[:600]}]}, timeout=40)
            if r.status_code == 200:
                try: return float(r.json()["choices"][0]["message"]["content"])
                except Exception: return None
            if r.status_code == 429: await asyncio.sleep(float(r.headers.get("retry-after", 3)) + 0.5); continue
            return None
    return None

async def main() -> None:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    import random
    def sub(name, texts, labels, n_per_class):                      # deterministic stratified subsample (documented in the VR)
        r = random.Random(7); out = []
        for k in (0, 1):
            ix = [i for i, l in enumerate(labels) if l == k]; r.shuffle(ix); out += [texts[i] for i in ix[:n_per_class]]
        return out
    S = B.load_sets(); texts = []
    texts += S["deepset_train"][0] + S["deepset_test"][0] + S["ours_holdout_attacks"][0] + S["ours_benign_indomain"][0]
    texts += sub("xtram_train", *S["xtram_train"], 300) + sub("xtram_test", *S["xtram_test"], 350) + new_benign()[:240]
    texts = sorted(set(texts), key=lambda x: key(x))
    todo = [t for t in texts if key(t) not in cache]; print(f"{len(texts)} texts, {len(todo)} to score", flush=True)
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient() as c:
        for i in range(0, len(todo), 200):
            res = await asyncio.gather(*(score(c, t, sem) for t in todo[i:i + 200]))
            for t, r in zip(todo[i:i + 200], res):
                if r is not None: cache[key(t)] = r
            CACHE.write_text(json.dumps(cache)); print(f"  {min(i + 200, len(todo))}/{len(todo)}", flush=True)

asyncio.run(main())
