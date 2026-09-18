"""Measured Moss latency for Precedent checks. Writes the `moss` section of docs/bench.json (`BENCH_KEY` / `BENCH_MACHINE` override it for a hosted run).

    uv run --env-file .env python eval/bench_latency.py

Reports two regimes on purpose: back-to-back (a busy agent) and spaced (a realistic agent, one
call every 300 ms). On the dev laptop the spaced regime is slower; we report both, not the best.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precedent.config import Settings  # noqa: E402
from precedent.decide import DecisionConfig  # noqa: E402
from precedent.featurize import build_features  # noqa: E402
from precedent.guard import Guard  # noqa: E402
from precedent.index import PrecedentIndex, load_jsonl, make_record  # noqa: E402
from precedent.schema import ProposedAction  # noqa: E402


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 2)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95), "p99": pct(xs, 0.99), "max": round(max(xs), 2)}


async def main() -> None:
    s = Settings.from_env()
    idx = await PrecedentIndex.open_moss(s.moss_project_id, s.moss_project_key, "precedent-bench", alpha=0.5, top_k=8)
    await idx.seed(load_jsonl(ROOT / "data" / "seed_actions.jsonl"))
    guard = Guard(idx, DecisionConfig(), budget_ms=s.budget_ms)
    items = [ProposedAction(**i["action"]) for i in load_jsonl(ROOT / "data" / "heldout.jsonl")]
    rnd = random.Random(7)

    for _ in range(50):  # warm-up, not measured
        await guard.evaluate(rnd.choice(items))

    burst_moss, burst_total = [], []
    for _ in range(1000):
        d = await guard.evaluate(rnd.choice(items))
        burst_moss.append(d.moss_ms)
        burst_total.append(d.latency_ms)

    spaced_moss, spaced_total, over = [], [], 0
    for _ in range(100):
        d = await guard.evaluate(rnd.choice(items))
        spaced_moss.append(d.moss_ms)
        spaced_total.append(d.latency_ms)
        over += not d.within_budget
        await asyncio.sleep(0.3)

    writes = []
    for i in range(20):
        a = rnd.choice(items)
        f = build_features(a)
        rec = make_record(f"bench-{i}", f.text, a.tool, "block", f.sig, f.prov_class, "bench", "human")
        writes.append(await idx.add(rec))

    out = {
        "machine": os.getenv("BENCH_MACHINE") or f"{platform.machine()} {platform.system()} (developer laptop)",
        "index_docs": idx.doc_count - len(writes),
        "budget_ms": s.budget_ms,
        "burst_1000": {"moss_query_ms": summary(burst_moss), "whole_check_ms": summary(burst_total)},
        "spaced_300ms_100": {
            "moss_query_ms": summary(spaced_moss), "whole_check_ms": summary(spaced_total),
            "over_budget": over,
        },
        "writeback_insert_to_retrievable_ms": summary(writes),
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = ROOT / "docs" / "bench.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    prev[os.getenv("BENCH_KEY", "moss")] = out
    path.write_text(json.dumps(prev, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
