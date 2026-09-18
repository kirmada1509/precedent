from precedent.featurize import build_features
from precedent.index import make_record
from precedent.schema import ProposedAction


async def test_human_adjudication_is_live_and_moves_the_decision(guard):
    a = ProposedAction(
        tool="issue_refund", args={"amount": 3000.0, "order_id": "ORD-5555"},
        arg_prov={"amount": "tool_output", "order_id": "tool_output"},
        context="very large refund", checks={"order_found": True, "customer_match": True},
    )
    before = (await guard.check(a)).decision
    f = build_features(a)
    rec = make_record("human-1", f.text, a.tool, "block", f.sig, f.prov_class, "over_limit", "human")
    ms = await guard.index.add(rec)
    after = (await guard.check(a)).decision
    assert ms < 500
    assert after.verdict == "block" and after.p_block >= before.p_block
    assert any(p.id == "human-1" for p in after.precedents)
