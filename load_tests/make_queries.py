#!/usr/bin/env python3
"""Build load_tests/queries.json: the spec's LOT-88392 query (5%) + a realistic mix drawn from the golden set (95%)."""
import json, random
G = [json.loads(l) for l in open("eval/data/golden_250.jsonl")]
pool = [g["question"] for g in G]
random.Random(1).shuffle(pool)
spec = "Verify batch LOT-88392 safety protocol and return triage status"
out = pool[:190] + [spec] * 10
random.Random(2).shuffle(out)
json.dump(out, open("load_tests/queries.json", "w"))
print(len(out), "queries;", out.count(spec), "are the spec query")
