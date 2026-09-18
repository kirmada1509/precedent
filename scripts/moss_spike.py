"""Phase 0 gate: prove the Moss session API does what Precedent needs.

Checks, in order:
  1. a local session can be opened and seeded
  2. hybrid query with a metadata filter works on a session
  3. a doc added after seeding is queryable immediately (write-back)
  4. warm query latency (p50/p95) and the score range for near-duplicate vs unrelated

Run:  MOSS_PROJECT_ID=... MOSS_PROJECT_KEY=... uv run python scripts/moss_spike.py
"""

import asyncio
import os
import statistics
import sys
import time

from moss import DocumentInfo, MossClient, QueryOptions

INDEX_NAME = "precedent-spike"

SEED = [
    ("tool=search_orders | args=email:str | prov=user_ticket:TRUSTED", "search_orders", "allow"),
    ("tool=get_customer | args=customer_id:id | prov=user_ticket:TRUSTED", "get_customer", "allow"),
    ("tool=issue_refund | args=amount:num,order_id:id | prov=user_ticket:TRUSTED", "issue_refund", "allow"),
    ("tool=issue_refund | args=amount:num,order_id:id | prov=agent_derived:TRUSTED", "issue_refund", "allow"),
    ("tool=issue_refund | args=amount:num,card:str,order_id:ABSENT | prov=email_body:UNTRUSTED", "issue_refund", "block"),
    ("tool=issue_refund | args=amount:num,card:str,order_id:ABSENT | prov=email_body:UNTRUSTED ctx=goodwill", "issue_refund", "block"),
    ("tool=send_email | args=to:internal,body:str | prov=agent_derived:TRUSTED", "send_email", "allow"),
    ("tool=send_email | args=to:external,body:str | prov=email_body:UNTRUSTED", "send_email", "block"),
    ("tool=export_customer_data | args=customer_id:id,dest:external | prov=email_body:UNTRUSTED", "export_customer_data", "block"),
    ("tool=close_ticket | args=ticket_id:id | prov=user_ticket:TRUSTED", "close_ticket", "allow"),
]

PROBE_INJECTION = "tool=issue_refund | args=amount:num,card:str,order_id:ABSENT | prov=email_body:UNTRUSTED"
PROBE_BENIGN = "tool=issue_refund | args=amount:num,order_id:id | prov=user_ticket:TRUSTED"
PROBE_UNRELATED = "tool=rotate_api_key | args=service:str | prov=agent_derived:TRUSTED"


def docs() -> list[DocumentInfo]:
    return [
        DocumentInfo(
            id=f"seed-{i}",
            text=text,
            metadata={"tool": tool, "verdict": verdict},
        )
        for i, (text, tool, verdict) in enumerate(SEED)
    ]


def show(label: str, result) -> None:
    print(f"\n{label}  ({result.time_taken_ms} ms)")
    for d in result.docs:
        print(f"  {d.score:7.3f}  {d.metadata.get('verdict', '?'):5}  {d.text[:88]}")


async def main() -> int:
    project_id = os.getenv("MOSS_PROJECT_ID")
    project_key = os.getenv("MOSS_PROJECT_KEY")
    if not project_id or not project_key:
        print("Set MOSS_PROJECT_ID and MOSS_PROJECT_KEY (from the Moss portal).")
        return 2

    client = MossClient(project_id, project_key)

    print("1. open session + seed")
    session = await client.session(index_name=INDEX_NAME)
    t0 = time.perf_counter()
    added, updated = await session.add_docs(docs())
    print(f"   added={added} updated={updated} in {(time.perf_counter() - t0) * 1000:.1f} ms")

    print("2. alpha sweep, tool-filtered")
    tool_filter = {"field": "tool", "condition": {"$eq": "issue_refund"}}
    for alpha in (0.3, 0.5, 0.8):
        res = await session.query(PROBE_INJECTION, QueryOptions(top_k=4, alpha=alpha, filter=tool_filter))
        show(f"injection probe, alpha={alpha}", res)
    res = await session.query(PROBE_UNRELATED, QueryOptions(top_k=3, alpha=0.5))
    show("unrelated probe (no filter)", res)

    print("\n3. write-back: add a doc, query immediately")
    new = DocumentInfo(
        id="writeback-1",
        text="tool=issue_refund | args=amount:num,card:str,order_id:ABSENT | prov=email_body:UNTRUSTED ctx=urgent",
        metadata={"tool": "issue_refund", "verdict": "block"},
    )
    t0 = time.perf_counter()
    await session.add_docs([new])
    res = await session.query(PROBE_INJECTION, QueryOptions(top_k=4, alpha=0.5, filter=tool_filter))
    insert_to_query_ms = (time.perf_counter() - t0) * 1000
    hit = any(d.id == "writeback-1" for d in res.docs)
    print(f"   insert->queryable {insert_to_query_ms:.1f} ms, new doc retrieved: {hit}")

    print("4. warm latency, 200 queries")
    walls, reported = [], []
    for probe in (PROBE_INJECTION, PROBE_BENIGN) * 100:
        t0 = time.perf_counter()
        r = await session.query(probe, QueryOptions(top_k=8, alpha=0.5, filter=tool_filter))
        walls.append((time.perf_counter() - t0) * 1000)
        reported.append(r.time_taken_ms)
    walls.sort()
    reported.sort()

    def pct(xs: list[float], p: float) -> float:
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    print(f"   wall  p50={pct(walls, .5):.2f} p95={pct(walls, .95):.2f} p99={pct(walls, .99):.2f} ms")
    print(f"   moss  p50={pct(reported, .5):.2f} p95={pct(reported, .95):.2f} (time_taken_ms) mean={statistics.mean(reported):.2f}")

    ok = hit
    print("\nGATE:", "PASS" if ok else "FAIL — write-back not visible; use rebuild-on-adjudication fallback")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
