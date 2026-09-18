"""Turn a proposed action into the short, deterministic text Moss indexes and queries.

Design notes (from the Phase 0 spike): Moss scores are compressed, so the text must be short and
the discriminating tokens (provenance class, absent-but-expected args, value buckets, harness
checks) must be explicit words. Free-text context goes last and is capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from precedent.schema import TRUSTED_SOURCES, UNTRUSTED_SOURCES, ProposedAction

INTERNAL_DOMAINS = ("acme.example", "acme-support.example")

# Arguments each known tool is expected to carry. Missing ones are marked ABSENT.
TOOL_EXPECTED: dict[str, list[str]] = {
    "read_ticket": ["ticket_id"],
    "search_orders": ["email"],
    "get_customer": ["customer_id"],
    "issue_refund": ["amount", "order_id"],
    "send_email": ["to", "body"],
    "update_address": ["customer_id", "address"],
    "close_ticket": ["ticket_id"],
    "export_customer_data": ["customer_id", "dest"],
    "escalate_to_human": ["ticket_id", "reason"],
}

CARD_NAMES = {"card", "card_last4", "iban", "account", "account_number"}
EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+)$")
ID_RE = re.compile(r"^[A-Za-z]{1,6}-\d{2,}$")
MONEY_RE = re.compile(r"^[£$€]?\s*\d+(?:\.\d+)?$")
CTX_WORDS = 12

# Order in which a trusted provenance label is reported when several are present.
_TRUSTED_ORDER = ("user_ticket", "tool_output", "system", "agent_derived")


def bucket(x: float) -> str:
    x = abs(x)
    for limit, name in ((10, "lt10"), (50, "lt50"), (200, "lt200"), (1000, "lt1k")):
        if x < limit:
            return name
    return "gte1k"


def _is_internal(host: str) -> bool:
    host = host.lower().strip("/")
    return any(host == d or host.endswith("." + d) for d in INTERNAL_DOMAINS)


def shape_of(name: str, value: Any) -> str:
    """Abstract a concrete argument value to its shape, keeping only what matters for judgement."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return f"num.{bucket(float(value))}"
    s = str(value).strip()
    if name in CARD_NAMES:
        return "card"
    if MONEY_RE.match(s):
        return f"num.{bucket(float(re.sub(r'[^0-9.]', '', s)))}"
    m = EMAIL_RE.match(s)
    if m:
        return "email.int" if _is_internal(m.group(1)) else "email.ext"
    if "://" in s:
        host = s.split("://", 1)[1].split("/", 1)[0]
        return "url.int" if _is_internal(host) else "url.ext"
    if ID_RE.match(s):
        return "id"
    return "str"


@dataclass
class Features:
    tool: str
    args: dict[str, str]  # arg name -> shape ("ABSENT" if expected but missing)
    prov_source: str  # e.g. "email_body"
    prov_class: str  # "TRUSTED" | "UNTRUSTED"
    tainted: list[str]
    checks: dict[str, str]
    context: str
    text: str = ""
    sig: str = field(default="")


def _clean_context(ctx: str) -> str:
    words = re.sub(r"[^a-z0-9£$€ ]+", " ", ctx.lower()).split()
    return " ".join(words[:CTX_WORDS])


def build_features(action: ProposedAction) -> Features:
    expected = TOOL_EXPECTED.get(action.tool, [])
    args: dict[str, str] = {}
    for name in expected:
        args[name] = shape_of(name, action.args[name]) if name in action.args else "ABSENT"
    for name in sorted(action.args):
        if name not in args:
            args[name] = shape_of(name, action.args[name])

    tainted = sorted(a for a in action.args if action.arg_prov.get(a) in UNTRUSTED_SOURCES)
    if tainted:
        prov_source = next(action.arg_prov[a] for a in tainted)
        prov_class = "UNTRUSTED"
    else:
        present = {action.arg_prov.get(a, "agent_derived") for a in action.args}
        prov_source = next((s for s in _TRUSTED_ORDER if s in present), "agent_derived")
        prov_class = "TRUSTED"

    checks = {k: ("yes" if v else "no") for k, v in sorted(action.checks.items())}
    context = _clean_context(action.context)

    parts = [
        f"tool={action.tool}",
        f"args={','.join(f'{k}:{v}' for k, v in args.items())}",
        f"prov={prov_source}:{prov_class}",
    ]
    if tainted:
        parts.append(f"tainted={','.join(tainted)}")
    if checks:
        parts.append(f"chk={','.join(f'{k}:{v}' for k, v in checks.items())}")
    if context:
        parts.append(f"ctx={context}")

    # The coarse signature is the exact-match part of "hybrid": same tool, same argument
    # names (incl. absent-but-expected), same provenance class, same harness check results.
    # Values and context are not in it. Checks are deterministic facts, so they are matched
    # exactly rather than left to the embedding, which barely notices a one-token yes/no flip.
    names = ",".join(sorted(k + ("!" if v == "ABSENT" else "") for k, v in args.items()))
    chk_sig = ",".join(f"{k}:{v}" for k, v in checks.items())
    return Features(
        tool=action.tool,
        args=args,
        prov_source=prov_source,
        prov_class=prov_class,
        tainted=tainted,
        checks=checks,
        context=context,
        text=" | ".join(parts),
        sig=f"{action.tool}|{names}|{prov_class}" + (f"|{chk_sig}" if chk_sig else ""),
    )


def parse_text(text: str) -> dict[str, str]:
    """Inverse of the canonical text: {"tool": ..., "args": ..., "prov": ..., ...}."""
    out: dict[str, str] = {}
    for part in text.split(" | "):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out
