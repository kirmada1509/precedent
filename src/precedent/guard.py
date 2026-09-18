"""The interceptor: every proposed action goes through `Guard.check` before it can run.

Fail-closed by construction: if retrieval errors, exceeds its budget, or the kill switch is on,
the action is blocked. There is no code path where a failure produces `allow`.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import deque
from typing import Any, Awaitable, Callable

from precedent.decide import DecisionConfig, decide
from precedent.evidence import build_card
from precedent.featurize import build_features
from precedent.index import PrecedentIndex
from precedent.schema import Decision, EvidenceCard, ProposedAction


class ActionBlocked(Exception):
    """Raised by `@guarded` tools when the guard does not allow the call."""

    def __init__(self, card: EvidenceCard):
        super().__init__(card.headline)
        self.card = card


class Guard:
    def __init__(
        self,
        index: PrecedentIndex,
        cfg: DecisionConfig | None = None,
        budget_ms: float = 25.0,
        on_card: Callable[[EvidenceCard], Awaitable[None] | None] | None = None,
    ):
        self.index = index
        self.cfg = cfg or DecisionConfig()
        self.budget_ms = budget_ms
        self.kill_switch = False
        self.on_card = on_card
        self.check_ms: deque[float] = deque(maxlen=2000)
        self.moss_ms: deque[float] = deque(maxlen=2000)
        self.counts = {"allow": 0, "block": 0, "escalate": 0, "fail_closed": 0}

    def _fail_closed(self, action: ProposedAction, code: str, why: str, t0: float) -> Decision:
        return Decision(
            action=action, verdict="block", confidence=1.0, p_block=1.0, reason_code=code,
            reason=why, latency_ms=(time.perf_counter() - t0) * 1000, within_budget=False,
            fail_closed=True,
        )

    async def evaluate(self, action: ProposedAction) -> Decision:
        t0 = time.perf_counter()
        if self.kill_switch:
            return self._fail_closed(action, "kill_switch", "Kill switch is on: all actions are blocked.", t0)

        feats = build_features(action)
        try:
            out = await asyncio.wait_for(
                self.index.query(feats.text, action.tool), timeout=self.budget_ms / 1000
            )
        except asyncio.TimeoutError:
            return self._fail_closed(
                action, "fail_closed_timeout",
                f"Precedent lookup exceeded the {self.budget_ms:.0f} ms budget; blocked.", t0,
            )
        except Exception as exc:  # noqa: BLE001 - any retrieval failure must fail closed
            return self._fail_closed(
                action, "fail_closed_error", f"Precedent lookup failed ({type(exc).__name__}); blocked.", t0
            )

        try:
            outcome = decide(feats.sig, out.tool_known, out.precedents, self.cfg)
        except Exception as exc:  # noqa: BLE001
            return self._fail_closed(
                action, "fail_closed_error", f"Decision rule failed ({type(exc).__name__}); blocked.", t0
            )

        latency = (time.perf_counter() - t0) * 1000
        best = lambda v: next((round(p.score, 4) for p in out.precedents if p.verdict == v), None)  # noqa: E731
        return Decision(
            nearest_block=best("block"), nearest_allow=best("allow"),
            action=action, verdict=outcome.verdict, confidence=round(outcome.confidence, 4),
            p_block=round(outcome.p_block, 4), reason_code=outcome.reason_code,
            reason=outcome.reason, precedents=outcome.precedents, text=feats.text, sig=feats.sig,
            moss_ms=round(out.moss_ms, 3), latency_ms=round(latency, 3),
            within_budget=latency <= self.budget_ms,
        )

    async def check(self, action: ProposedAction) -> EvidenceCard:
        decision = await self.evaluate(action)
        self.check_ms.append(decision.latency_ms)
        if decision.moss_ms:
            self.moss_ms.append(decision.moss_ms)
        self.counts[decision.verdict] += 1
        if decision.fail_closed:
            self.counts["fail_closed"] += 1
        card = build_card(decision)
        if self.on_card is not None:
            res = self.on_card(card)
            if asyncio.iscoroutine(res):
                await res
        return card

    def stats(self) -> dict[str, Any]:
        def pct(xs: list[float], p: float) -> float:
            return round(xs[min(len(xs) - 1, int(len(xs) * p))], 3) if xs else 0.0

        chk, moss = sorted(self.check_ms), sorted(self.moss_ms)
        total = sum(self.counts[v] for v in ("allow", "block", "escalate"))
        return {
            "checked": total, **self.counts,
            "check_ms": {"p50": pct(chk, 0.5), "p95": pct(chk, 0.95), "p99": pct(chk, 0.99), "n": len(chk)},
            "moss_ms": {"p50": pct(moss, 0.5), "p95": pct(moss, 0.95), "p99": pct(moss, 0.99), "n": len(moss)},
            "budget_ms": self.budget_ms, "kill_switch": self.kill_switch,
            "index_docs": self.index.doc_count,
        }


def guarded(
    guard: Guard,
    tool: str,
    build_action: Callable[..., ProposedAction],
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Wrap an async tool so it only runs when the guard allows it; otherwise raise `ActionBlocked`."""

    def wrap(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def inner(*args: Any, **kwargs: Any) -> Any:
            card = await guard.check(build_action(tool, *args, **kwargs))
            if card.decision.verdict != "allow":
                raise ActionBlocked(card)
            return await fn(*args, **kwargs)

        return inner

    return wrap
