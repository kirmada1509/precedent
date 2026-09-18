"""The precedent index: a local, in-process Moss session holding adjudicated actions.

Every query and every write goes through one `SessionLike`. In production that is a Moss
`SessionIndex` (hybrid retrieval, local embedding, no network hop per query). Unit tests inject
`MemorySession`, a tiny stand-in with the same surface.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Protocol

from precedent.schema import Precedent


class SessionLike(Protocol):
    async def add_docs(self, docs: list[Any], options: Any = None) -> tuple[int, int]: ...
    async def query(self, query: str, options: Any = None) -> Any: ...
    async def delete_docs(self, doc_ids: list[str]) -> int: ...


@dataclass
class QueryOutcome:
    precedents: list[Precedent]
    tool_known: bool
    moss_ms: float


# Only these travel to Moss. Phase-0 finding: metadata is folded into scoring, so extra fields
# (notes, timestamps, signatures) distort ranking, and an identical precedent carrying a note
# scored 0.956 versus 1.000 without one. Everything else lives in a local registry keyed by id.
MOSS_FIELDS = ("tool", "verdict")


def record_to_precedent(rec: dict[str, Any], score: float = 0.0) -> Precedent:
    md = rec["metadata"]
    return Precedent(
        id=rec["id"], text=rec["text"], tool=md["tool"], verdict=md["verdict"],
        reason_code=md.get("reason_code", ""), sig=md.get("sig", ""),
        prov_class=md.get("prov_class", ""), source=md.get("source", "seed"),
        note=md.get("note", ""), score=score,
    )


def make_record(
    rec_id: str, text: str, tool: str, verdict: str, sig: str, prov_class: str,
    reason_code: str = "", source: str = "seed", note: str = "",
) -> dict[str, Any]:
    """A document as stored in Moss. Metadata values must all be strings."""
    return {
        "id": rec_id,
        "text": text,
        "metadata": {
            "tool": tool, "verdict": verdict, "reason_code": reason_code, "sig": sig,
            "prov_class": prov_class, "source": source, "note": note,
            "ts": str(int(time.time())),
        },
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class PrecedentIndex:
    def __init__(self, session: SessionLike | None = None, *, alpha: float = 0.5, top_k: int = 8):
        self._session = session
        self.alpha = alpha
        self.top_k = top_k
        self.doc_count = 0
        self._meta: dict[str, dict[str, str]] = {}  # doc id -> full metadata (not sent to Moss)
        self._tool_counts: dict[str, int] = {}
        self.ready = session is not None

    # -- lifecycle ---------------------------------------------------------------------------
    @classmethod
    async def open_moss(cls, project_id: str, project_key: str, index_name: str, **kw: Any) -> "PrecedentIndex":
        from moss import MossClient  # imported lazily so tests never need the SDK to connect

        client = MossClient(project_id, project_key)
        session = await client.session(index_name=index_name)
        return cls(session, **kw)

    async def seed(self, records: Iterable[dict[str, Any]], batch: int = 100) -> int:
        from moss import DocumentInfo

        recs = list(records)
        for i in range(0, len(recs), batch):
            chunk = recs[i : i + batch]
            await self._session.add_docs([self._doc(DocumentInfo, r) for r in chunk])
            for r in chunk:
                self._meta[r["id"]] = r["metadata"]
                self._count(r["metadata"]["tool"], +1)
        self.ready = True
        return len(recs)

    @staticmethod
    def _doc(doc_cls: Any, rec: dict[str, Any]) -> Any:
        slim = {k: rec["metadata"][k] for k in MOSS_FIELDS}
        return doc_cls(id=rec["id"], text=rec["text"], metadata=slim)

    def _count(self, tool: str, delta: int) -> None:
        self._tool_counts[tool] = self._tool_counts.get(tool, 0) + delta
        self.doc_count += delta

    # -- hot path ----------------------------------------------------------------------------
    async def query(self, text: str, tool: str) -> QueryOutcome:
        from moss import QueryOptions

        opts = QueryOptions(
            top_k=self.top_k,
            alpha=self.alpha,
            filter={"field": "tool", "condition": {"$eq": tool}},
        )
        t0 = time.perf_counter()
        res = await self._session.query(text, opts)
        moss_ms = (time.perf_counter() - t0) * 1000
        precedents = [
            record_to_precedent(
                {"id": d.id, "text": d.text, "metadata": {**d.metadata, **self._meta.get(d.id, {})}}, d.score
            )
            for d in res.docs
        ]
        return QueryOutcome(precedents, self._tool_counts.get(tool, 0) > 0, moss_ms)

    # -- write-back --------------------------------------------------------------------------
    async def add(self, rec: dict[str, Any]) -> float:
        """Insert one adjudicated action. Returns insert -> retrievable, in ms.

        The probe query is what makes the number honest: it proves the new precedent is actually
        returned, not merely accepted.
        """
        from moss import DocumentInfo

        t0 = time.perf_counter()
        await self._session.add_docs([self._doc(DocumentInfo, rec)])
        self._meta[rec["id"]] = rec["metadata"]
        self._count(rec["metadata"]["tool"], +1)
        out = await self.query(rec["text"], rec["metadata"]["tool"])
        visible = any(p.id == rec["id"] for p in out.precedents)
        ms = (time.perf_counter() - t0) * 1000
        if not visible:
            raise RuntimeError(f"precedent {rec['id']} not retrievable after insert")
        return ms

    async def remove(self, rec_ids: list[str], tools: list[str]) -> None:
        if rec_ids:
            await self._session.delete_docs(rec_ids)
            for i in rec_ids:
                self._meta.pop(i, None)
            for t in tools:
                self._count(t, -1)


# -- in-memory stand-in for unit tests -------------------------------------------------------
_TOK = re.compile(r"[a-z0-9_.:!£$€]+")


class MemorySession:
    """Token-overlap scorer with Moss's call surface (add_docs/query/delete_docs + metadata filter)."""

    def __init__(self) -> None:
        self.docs: dict[str, Any] = {}

    async def add_docs(self, docs: list[Any], options: Any = None) -> tuple[int, int]:
        added = sum(1 for d in docs if d.id not in self.docs)
        for d in docs:
            self.docs[d.id] = d
        return added, len(docs) - added

    async def delete_docs(self, doc_ids: list[str]) -> int:
        return sum(1 for i in doc_ids if self.docs.pop(i, None) is not None)

    async def query(self, query: str, options: Any = None) -> Any:
        q = set(_TOK.findall(query.lower()))
        flt = getattr(options, "filter", None)
        scored = []
        for d in self.docs.values():
            if flt and d.metadata.get(flt["field"]) != flt["condition"]["$eq"]:
                continue
            t = set(_TOK.findall(d.text.lower()))
            j = len(q & t) / max(1, len(q | t))
            scored.append(SimpleNamespace(id=d.id, text=d.text, metadata=d.metadata, score=j))
        scored.sort(key=lambda d: -d.score)
        k = getattr(options, "top_k", None) or 8
        return SimpleNamespace(docs=scored[:k], time_taken_ms=0)
