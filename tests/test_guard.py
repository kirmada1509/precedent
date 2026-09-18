import asyncio

import pytest

from precedent.guard import ActionBlocked, guarded
from precedent.schema import ProposedAction


def injected():
    return ProposedAction(
        tool="issue_refund", args={"amount": 400, "card": "4412"},
        arg_prov={"amount": "email_body", "card": "email_body"},
        context="goodwill refund request", checks={"order_found": False},
    )


def benign():
    return ProposedAction(
        tool="issue_refund", args={"amount": 18.5, "order_id": "ORD-1002"},
        arg_prov={"amount": "tool_output", "order_id": "tool_output"},
        context="damaged item refund", checks={"order_found": True, "customer_match": True},
    )


async def test_blocks_injection_allows_benign(guard):
    assert (await guard.check(injected())).decision.verdict == "block"
    assert (await guard.check(benign())).decision.verdict == "allow"


async def test_unknown_tool_escalates_not_guesses(guard):
    a = ProposedAction(tool="transfer_funds", args={"amount": 900}, arg_prov={"amount": "agent_derived"})
    d = (await guard.check(a)).decision
    assert d.verdict == "escalate" and d.reason_code == "no_precedent"


async def test_fail_closed_on_index_error(guard):
    async def boom(*a, **k):
        raise RuntimeError("moss down")

    guard.index.query = boom
    d = (await guard.check(benign())).decision
    assert d.verdict == "block" and d.fail_closed and d.reason_code == "fail_closed_error"


async def test_fail_closed_on_timeout(guard):
    async def slow(*a, **k):
        await asyncio.sleep(1)

    guard.index.query = slow
    guard.budget_ms = 20
    d = (await guard.check(benign())).decision
    assert d.verdict == "block" and d.reason_code == "fail_closed_timeout"


async def test_kill_switch_blocks_everything(guard):
    guard.kill_switch = True
    d = (await guard.check(benign())).decision
    assert d.verdict == "block" and d.reason_code == "kill_switch"


async def test_decorator_runs_tool_only_when_allowed(guard):
    ran = []

    def build(tool, **kw):
        return benign() if kw["ok"] else injected()

    @guarded(guard, "issue_refund", build)
    async def refund(ok):
        ran.append(ok)
        return "refunded"

    assert await refund(ok=True) == "refunded"
    with pytest.raises(ActionBlocked):
        await refund(ok=False)
    assert ran == [True]


async def test_stats_track_every_check(guard):
    await guard.check(benign())
    await guard.check(injected())
    s = guard.stats()
    assert s["checked"] == 2 and s["allow"] == 1 and s["block"] == 1 and s["check_ms"]["n"] == 2
