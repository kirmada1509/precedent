"""The scripted support shift end to end: harness provenance -> guard -> tools, no network."""

import pytest

from demo.env import SupportEnv
from demo.runner import Toolbox, shift, variant


@pytest.fixture
def box(guard):
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    b = Toolbox(SupportEnv(), guard, emit, pace=0)
    b.events = events
    return b


async def test_shift_stops_the_injection_and_escalates_the_novel_action(box):
    await shift(box)
    desk = box.env.state()
    # only the legitimate refund ran; the £400 injected one never did
    assert [r["amount"] for r in desk["refunds_executed"]] == [18.5]
    verdicts = {}
    for kind, d in box.events:
        if kind == "agent" and d.get("kind") in ("result", "refused"):
            verdicts.setdefault(d["tool"], []).append(d.get("verdict", "allow"))
    assert verdicts["issue_refund"] == ["allow", "block"]
    assert verdicts["rotate_api_key"] == ["escalate"]
    assert box.guard.counts["allow"] >= 10 and box.guard.counts["block"] == 1


async def test_provenance_is_traced_not_hardcoded(box):
    cards = []
    box.guard.on_card = lambda c: cards.append(c)
    await shift(box)
    injected = next(c for c in cards if c.decision.action.tool == "issue_refund" and "card" in c.decision.action.args)
    a = injected.decision.action
    # the harness traced both values back to the email body; nothing in the script says so
    assert a.arg_prov == {"amount": "email_body", "card": "email_body"}
    assert a.checks == {"order_found": False}
    assert "order_id:ABSENT" in injected.decision.text and "prov=email_body:UNTRUSTED" in injected.decision.text
    legit = next(c for c in cards if c.decision.action.tool == "issue_refund" and "order_id" in c.decision.action.args)
    # the same order id also appears in the email, but an internal system corroborated it
    assert legit.decision.action.arg_prov["order_id"] == "tool_output"
    assert legit.decision.verdict == "allow"


async def test_human_block_teaches_the_novel_action_within_the_session(box):
    from precedent.featurize import build_features
    from precedent.index import make_record

    await shift(box)
    novel = next(d for k, d in box.events if k == "agent" and d.get("kind") == "refused" and d["tool"] == "rotate_api_key")
    assert novel["verdict"] == "escalate"
    # a human blocks it; the next identical call is blocked, not escalated
    from precedent.schema import ProposedAction
    a = ProposedAction(tool="rotate_api_key", args={"service": "billing"}, arg_prov={"service": "agent_derived"}, context="rotate billing credentials")
    f = build_features(a)
    await box.guard.index.add(make_record("human-t", f.text, a.tool, "block", f.sig, f.prov_class, "human_review", "human"))
    assert (await box.guard.check(a)).decision.verdict == "block"


async def test_variant_injection_is_also_stopped(box):
    await shift(box)
    before = len(box.env.refunds)
    await variant(box)
    assert len(box.env.refunds) == before
