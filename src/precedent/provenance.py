"""Argument provenance by value overlap.

The harness registers every piece of content the agent has seen, labelled by channel. When the
agent proposes an action, each argument value is traced back to where it appeared:

* found in trusted content (internal tool output, system data, the verified ticket header)
  -> trusted, even if it also appears in an email body (it is *corroborated*);
* found only in untrusted content (an email body, third-party output) -> tainted;
* found nowhere -> `agent_derived` (the agent computed or invented it).

Limits, stated on purpose: this is value-overlap, not information-flow tracking. A paraphrased or
re-encoded value ("four hundred pounds") has no overlap and shows up as `agent_derived`; the
precedent shape (e.g. no order id supplied) is what still catches it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from precedent.schema import TRUSTED_SOURCES, UNTRUSTED_SOURCES

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
MIN_STR_LEN = 4
MIN_INT_MATCH = 100  # small integers ("2", "10") appear everywhere; matching them only adds noise


def _matchable(num: float) -> bool:
    return num != int(num) or abs(num) >= MIN_INT_MATCH


@dataclass
class Chunk:
    source: str
    text: str
    lowered: str = field(init=False)
    numbers: set[float] = field(init=False)

    def __post_init__(self) -> None:
        self.lowered = self.text.lower()
        self.numbers = {float(n) for n in _NUM_RE.findall(self.text)}


class ProvenanceTracker:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    def register(self, text: str, source: str) -> None:
        if source not in TRUSTED_SOURCES | UNTRUSTED_SOURCES:
            raise ValueError(f"unknown provenance source: {source}")
        if text:
            self._chunks.append(Chunk(source, str(text)))

    def clear(self) -> None:
        self._chunks.clear()

    def _match(self, value: Any) -> list[Chunk]:
        if isinstance(value, bool):
            return []
        if isinstance(value, (int, float)):
            num = float(value)
            return [c for c in self._chunks if num in c.numbers] if _matchable(num) else []
        s = str(value).strip()
        nums = _NUM_RE.findall(s)
        if nums and re.fullmatch(r"[£$€]?\s*\d+(?:\.\d+)?", s):
            num = float(nums[0])
            return [c for c in self._chunks if num in c.numbers] if _matchable(num) else []
        if len(s) < MIN_STR_LEN:
            return []
        low = s.lower()
        return [c for c in self._chunks if low in c.lowered]

    def tag(self, args: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, value in args.items():
            hits = self._match(value)
            trusted = [c for c in hits if c.source in TRUSTED_SOURCES]
            if trusted:
                out[name] = trusted[-1].source
            elif hits:
                out[name] = hits[-1].source
            else:
                out[name] = "agent_derived"
        return out
