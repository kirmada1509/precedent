"""Nearest-precedent decision rule.

Moss scores are compressed (spike: 0.969 vs 0.939 for block vs allow), so nothing here compares a
score to an absolute threshold. Instead:

* novelty comes from exact facts: no precedent for the tool, or none in the top-k that shares the
  action's coarse signature (same tool, same argument names, same provenance class);
* the verdict comes from a kernel-weighted vote whose weights depend on the *gap to the best
  score*, so a small absolute margin still separates classes;
* the rule is asymmetric: a block needs little mass, an allow needs near-unanimity and support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from precedent.schema import Precedent, Verdict


@dataclass(frozen=True)
class DecisionConfig:
    vote_k: int = 5
    temperature: float = 0.02  # kernel width over the score gap to the best hit
    sig_bonus: float = 2.0  # weight multiplier for same-signature precedents
    # Neighbours with a different exact signature are a different situation (other harness checks,
    # other provenance, other arguments). They may inform, but must not outvote exact-shape ones.
    other_sig_weight: float = 0.15
    human_bonus: float = 1.5  # weight multiplier for human-adjudicated precedents (fresh)
    t_block: float = 0.35  # p_block at or above this -> block
    t_allow: float = 0.15  # p_block at or below this -> allow
    min_allow_support: int = 2  # same-signature allow precedents needed to allow


@dataclass
class Outcome:
    verdict: Verdict
    confidence: float
    p_block: float
    reason_code: str
    reason: str
    precedents: list[Precedent]  # top-k with .weight filled in


def _weights(top: list[Precedent], sig: str, cfg: DecisionConfig) -> list[float]:
    best = top[0].score
    raw = []
    for p in top:
        w = math.exp((p.score - best) / cfg.temperature)
        w *= cfg.sig_bonus if p.sig == sig else cfg.other_sig_weight
        if p.source == "human":
            w *= cfg.human_bonus
        raw.append(w)
    total = sum(raw) or 1.0
    return [w / total for w in raw]


def decide(sig: str, tool_known: bool, precedents: list[Precedent], cfg: DecisionConfig) -> Outcome:
    """`precedents` are already filtered to the action's tool and ranked best-first."""
    top = [p.model_copy() for p in precedents[: cfg.vote_k]]

    if not top or not tool_known:
        return Outcome(
            "escalate", 0.0, 0.5, "no_precedent",
            "No precedent for this tool. Not guessing: escalating to a human.", top,
        )

    same_sig = [p for p in top if p.sig == sig]
    if not same_sig:
        return Outcome(
            "escalate", 0.0, 0.5, "novel_shape",
            "Known tool, but no precedent with this argument shape and provenance. Escalating.", top,
        )

    for p, w in zip(top, _weights(top, sig, cfg)):
        p.weight = round(w, 4)
    p_block = sum(p.weight for p in top if p.verdict == "block")

    if p_block >= cfg.t_block:
        lead = max((p for p in top if p.verdict == "block"), key=lambda p: p.weight)
        return Outcome(
            "block", p_block, p_block, lead.reason_code or "blocked_precedent",
            f"{p_block:.0%} of precedent weight is blocked actions of this shape "
            f"(closest: {lead.reason_code or 'blocked'}).", top,
        )

    # An explicit human approval is enough support on its own; seed precedents count one each.
    allow_support = sum(
        cfg.min_allow_support if p.source == "human" else 1 for p in same_sig if p.verdict == "allow"
    )
    if p_block <= cfg.t_allow and allow_support >= cfg.min_allow_support:
        return Outcome(
            "allow", 1 - p_block, p_block, "approved_precedent",
            f"{1 - p_block:.0%} of precedent weight is approved actions of this shape.", top,
        )

    why = "thin allow support" if p_block <= cfg.t_allow else "precedents disagree"
    return Outcome(
        "escalate", max(p_block, 1 - p_block), p_block, "ambiguous",
        f"Not enough agreement to decide ({why}); p_block={p_block:.0%}. Escalating.", top,
    )


def with_overrides(cfg: DecisionConfig, **kw: float) -> DecisionConfig:
    return replace(cfg, **kw)
