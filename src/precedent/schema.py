"""Data contracts shared by the guard, the API and the demo."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["allow", "block", "escalate"]
PrecedentVerdict = Literal["allow", "block"]

# Where an argument value came from. Untrusted channels carry attacker-controllable text.
TRUSTED_SOURCES = {"user_ticket", "agent_derived", "system", "tool_output"}
UNTRUSTED_SOURCES = {"email_body", "external_tool_output"}


class ProposedAction(BaseModel):
    """One tool call an agent wants to make, before it runs."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # arg name -> source channel. Filled by the provenance tracker, not by the agent.
    arg_prov: dict[str, str] = Field(default_factory=dict)
    # Facts the harness verified about the call, e.g. {"order_found": False}.
    checks: dict[str, bool] = Field(default_factory=dict)
    context: str = ""
    session_id: str = "default"


class Precedent(BaseModel):
    """A past adjudicated action retrieved from the index."""

    id: str
    text: str
    tool: str
    verdict: PrecedentVerdict
    reason_code: str = ""
    sig: str = ""
    prov_class: str = ""
    source: str = "seed"  # "seed" or "human"
    note: str = ""
    score: float = 0.0
    weight: float = 0.0  # share of the decision this precedent carried, 0..1


class Decision(BaseModel):
    """The guard's answer for one proposed action."""

    action: ProposedAction
    verdict: Verdict
    confidence: float
    p_block: float
    reason_code: str
    reason: str
    precedents: list[Precedent] = Field(default_factory=list)
    text: str = ""
    sig: str = ""
    nearest_block: float | None = None  # score of the closest blocked precedent
    nearest_allow: float | None = None  # score of the closest approved precedent
    moss_ms: float = 0.0  # wall clock around the Moss query only
    latency_ms: float = 0.0  # whole check: featurize + retrieve + decide
    within_budget: bool = True
    fail_closed: bool = False


class EvidenceCard(BaseModel):
    """Replayable record of a decision: what was proposed, what it was judged against, why."""

    card_id: str
    created_at: str
    decision: Decision
    headline: str
    top_precedents: list[Precedent]
    matched_features: list[str]
    differing_features: list[str]
    sha256: str
    adjudication: dict[str, Any] | None = None
    previous_confidence: float | None = None  # same action, before the human decision
    previous_verdict: str | None = None
    previous_p_block: float | None = None
