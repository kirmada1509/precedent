"""Evidence cards: the proposed action, the precedents it was judged against, and *which features matched*."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from precedent.featurize import parse_text
from precedent.schema import Decision, EvidenceCard, Precedent


def _tokens(value: str) -> set[str]:
    return {t for t in value.replace(" ", ",").split(",") if t}


def diff_features(action_text: str, prec_text: str) -> tuple[list[str], list[str]]:
    """Compare two canonical texts field by field. Returns (matched, differing) as readable strings."""
    a, b = parse_text(action_text), parse_text(prec_text)
    matched: list[str] = []
    differing: list[str] = []
    for field in ("args", "prov", "tainted", "chk"):
        av, bv = a.get(field), b.get(field)
        if av is None and bv is None:
            continue
        at, bt = _tokens(av or ""), _tokens(bv or "")
        both, only_a, only_b = sorted(at & bt), sorted(at - bt), sorted(bt - at)
        if both:
            matched.append(f"{field}: {', '.join(both)}")
        if only_a or only_b:
            differing.append(f"{field}: proposed {', '.join(only_a) or '—'} vs precedent {', '.join(only_b) or '—'}")
    return matched, differing


def _headline(decision: Decision, matched: list[str], lead: Precedent | None) -> str:
    v = decision.verdict
    if decision.fail_closed:
        return f"Fail-closed: {decision.reason}"
    if v == "escalate":
        return "No precedent, escalate" if decision.reason_code == "no_precedent" else decision.reason
    if lead is None:
        return decision.reason
    flat = " ".join(matched)
    if v == "block":
        if "UNTRUSTED" in flat:
            return "Blocked: an argument came from an untrusted channel, and no order id backs it"
        if "ABSENT" in flat or "!" in flat:
            return "Blocked: a required argument is missing, same as a blocked precedent"
        return f"Blocked: same shape as a blocked precedent ({lead.reason_code or 'blocked'})"
    return "Allowed: same shape as approved precedents"


def build_card(decision: Decision) -> EvidenceCard:
    top = decision.precedents[:3]
    lead = None
    if decision.verdict == "block":
        lead = next((p for p in decision.precedents if p.verdict == "block"), None)
    elif decision.verdict == "allow":
        lead = next((p for p in decision.precedents if p.verdict == "allow"), None)
    else:
        lead = decision.precedents[0] if decision.precedents else None

    matched: list[str] = []
    differing: list[str] = []
    if lead is not None and decision.text:
        matched, differing = diff_features(decision.text, lead.text)

    created = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    body = {
        "action": decision.action.model_dump(),
        "verdict": decision.verdict,
        "reason_code": decision.reason_code,
        "p_block": round(decision.p_block, 4),
        "precedents": [p.id for p in top],
        "matched": matched,
        "created_at": created,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return EvidenceCard(
        card_id=digest[:12],
        created_at=created,
        decision=decision,
        headline=_headline(decision, matched, lead),
        top_precedents=top,
        matched_features=matched,
        differing_features=differing,
        sha256=digest,
    )
