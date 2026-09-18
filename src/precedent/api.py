"""HTTP surface: check, adjudicate, recheck, live event stream, stats, kill switch, demo control."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import fields
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from demo.env import TICKETS, SupportEnv
from demo.runner import SCENARIOS, Toolbox
from precedent.audit import Audit
from precedent.config import ROOT, Settings
from precedent.decide import DecisionConfig
from precedent.featurize import build_features
from precedent.guard import Guard
from precedent.index import PrecedentIndex, load_jsonl, make_record
from precedent.schema import EvidenceCard, ProposedAction


class AdjudicateIn(BaseModel):
    card_id: str
    verdict: Literal["allow", "block"]
    reason_code: str | None = None
    note: str = ""
    reviewer: str = "reviewer"


class RunIn(BaseModel):
    scenario: Literal["shift", "variant", "gemini"] = "shift"
    pace: float = 0.6


class KillIn(BaseModel):
    active: bool


def load_calibration() -> tuple[DecisionConfig, float | None]:
    path = ROOT / "data" / "calibrated.json"
    if not path.exists():
        return DecisionConfig(), None
    raw = json.loads(path.read_text())
    alpha = raw.get("alpha")
    names = {f.name for f in fields(DecisionConfig)}
    return DecisionConfig(**{k: v for k, v in raw.items() if k in names}), alpha


class State:
    """Everything the running service owns. One instance, created at startup."""

    def __init__(self) -> None:
        self.settings = Settings.from_env()
        self.audit = Audit(self.settings.data_dir)
        self.env = SupportEnv()
        self.cards: dict[str, EvidenceCard] = {}
        self.card_order: list[str] = []
        self.history: list[dict[str, Any]] = []
        self.subs: set[asyncio.Queue] = set()
        self.seq = 0
        self.human: list[dict[str, str]] = []  # [{"id":..., "tool":...}] for reset
        self.writeback_ms: list[float] = []
        self.run_task: asyncio.Task | None = None
        self.index: PrecedentIndex
        self.guard: Guard
        self.box: Toolbox

    async def emit(self, type_: str, data: dict[str, Any]) -> None:
        self.seq += 1
        ev = {"type": type_, "data": data, "seq": self.seq}
        self.history.append(ev)
        del self.history[:-600]
        for q in list(self.subs):
            q.put_nowait(ev)

    async def on_card(self, card: EvidenceCard) -> None:
        self.cards[card.card_id] = card
        self.card_order.append(card.card_id)
        self.audit.log_decision(json.loads(card.model_dump_json()))
        await self.emit("card", {"card": json.loads(card.model_dump_json()), "stats": self.guard.stats()})

    async def start(self) -> None:
        s = self.settings
        cfg, alpha = load_calibration()
        self.index = await PrecedentIndex.open_moss(
            s.moss_project_id, s.moss_project_key, s.index_name,
            alpha=alpha if alpha is not None else s.alpha, top_k=s.top_k,
        )
        await self.index.seed(load_jsonl(ROOT / "data" / "seed_actions.jsonl"))
        for rec in self.audit.adjudication_records():  # human decisions survive restarts
            await self.index.add(rec)
            self.human.append({"id": rec["id"], "tool": rec["metadata"]["tool"]})
        self.guard = Guard(self.index, cfg, budget_ms=s.budget_ms, on_card=self.on_card)
        self.box = Toolbox(self.env, self.guard, self.emit)
        await self.warm_up()

    async def warm_up(self) -> None:
        """First queries pay one-off costs; burn them before serving so the budget means something."""
        probe = ProposedAction(tool="search_orders", args={"email": "warm@mail.example"}, arg_prov={"email": "user_ticket"})
        f = build_features(probe)
        for _ in range(30):
            await self.index.query(f.text, probe.tool)

    async def reset(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            await asyncio.gather(self.run_task, return_exceptions=True)
        await self.index.remove([h["id"] for h in self.human], [h["tool"] for h in self.human])
        self.human.clear()
        self.audit.reset_adjudications()
        self.writeback_ms.clear()
        self.cards.clear()
        self.card_order.clear()
        self.history.clear()
        self.env.reset()
        self.guard.check_ms.clear()
        self.guard.moss_ms.clear()
        self.guard.counts = {"allow": 0, "block": 0, "escalate": 0, "fail_closed": 0}
        self.guard.kill_switch = False
        await self.emit("reset", {})


class NoCacheStatic(StaticFiles):
    """Always revalidate: the UI is three small files and a stale copy is worse than a 304."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def _card_json(card: EvidenceCard) -> dict[str, Any]:
    return json.loads(card.model_dump_json())


def create_app() -> FastAPI:
    state = State()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.start()
        yield
        if state.run_task:
            state.run_task.cancel()

    app = FastAPI(title="Precedent", version="0.1.0", lifespan=lifespan)
    app.state.p = state

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        ready = getattr(state, "index", None) is not None and state.index.ready
        return {"ok": ready, "index_ready": ready, "docs": state.index.doc_count if ready else 0}

    @app.post("/v1/check")
    async def check(action: ProposedAction) -> dict[str, Any]:
        return _card_json(await state.guard.check(action))

    @app.post("/v1/adjudicate")
    async def adjudicate(body: AdjudicateIn) -> dict[str, Any]:
        card = state.cards.get(body.card_id)
        if card is None:
            raise HTTPException(404, "unknown card")
        d = card.decision
        if not d.text:
            raise HTTPException(409, "fail-closed decisions have no features to learn from")
        if card.adjudication:
            raise HTTPException(409, "already adjudicated")
        feats = build_features(d.action)
        n = len(state.human) + 1
        reason = body.reason_code or (d.reason_code if body.verdict == "block" and d.reason_code not in ("no_precedent", "novel_shape", "ambiguous") else "human_review")
        rec = make_record(
            f"human-{n:04d}-{card.card_id}", feats.text, d.action.tool, body.verdict, feats.sig,
            feats.prov_class, reason, "human", body.note,
        )
        ms = await state.index.add(rec)
        state.audit.log_adjudication(rec)
        state.human.append({"id": rec["id"], "tool": d.action.tool})
        state.writeback_ms.append(ms)
        card.adjudication = {
            "verdict": body.verdict, "reason_code": reason, "reviewer": body.reviewer,
            "note": body.note, "insert_to_queryable_ms": round(ms, 2), "precedent_id": rec["id"],
        }
        await state.emit("adjudication", {"card_id": card.card_id, **card.adjudication, "index_docs": state.index.doc_count})
        return card.adjudication

    @app.post("/v1/cards/{card_id}/recheck")
    async def recheck(card_id: str) -> dict[str, Any]:
        old = state.cards.get(card_id)
        if old is None:
            raise HTTPException(404, "unknown card")
        new = await state.guard.check(old.decision.action)
        new.previous_confidence = old.decision.confidence
        new.previous_verdict = old.decision.verdict
        new.previous_p_block = old.decision.p_block
        await state.emit("recheck", {"from": old.card_id, "card": _card_json(new)})
        return _card_json(new)

    @app.get("/v1/cards/{card_id}")
    async def get_card(card_id: str) -> dict[str, Any]:
        c = state.cards.get(card_id)
        if c is None:
            raise HTTPException(404, "unknown card")
        return _card_json(c)

    @app.get("/v1/state")
    async def get_state() -> dict[str, Any]:
        return {
            "events": state.history[-400:], "stats": stats_payload(),
            "running": bool(state.run_task and not state.run_task.done()),
            "gemini": bool(state.settings.gemini_api_key),
            "tickets": {k: {"from": v["from"], "subject": v["subject"], "body": v["body"], "hidden": v.get("hidden")} for k, v in TICKETS.items()},
            "desk": state.env.state(),
        }

    def stats_payload() -> dict[str, Any]:
        out = state.guard.stats()
        wb = sorted(state.writeback_ms)
        out["writeback_ms"] = {"last": round(state.writeback_ms[-1], 2) if wb else None, "n": len(wb)}
        for name in ("eval", "bench"):
            p = ROOT / "docs" / f"{name}.json"
            out[name] = json.loads(p.read_text()) if p.exists() else None
        return out

    @app.get("/v1/stats")
    async def stats() -> dict[str, Any]:
        return stats_payload()

    @app.post("/v1/killswitch")
    async def killswitch(body: KillIn) -> dict[str, Any]:
        state.guard.kill_switch = body.active
        await state.emit("killswitch", {"active": body.active})
        return {"active": body.active}

    @app.post("/v1/demo/run")
    async def demo_run(body: RunIn) -> dict[str, Any]:
        if state.run_task and not state.run_task.done():
            raise HTTPException(409, "a run is already in progress")
        state.box.pace = body.pace
        if body.scenario == "gemini":
            from demo.agent_gemini import run_gemini

            fn = lambda box: run_gemini(box, state.settings)  # noqa: E731
        else:
            fn = SCENARIOS[body.scenario]

        async def go() -> None:
            await state.emit("run", {"state": "started", "scenario": body.scenario})
            try:
                await fn(state.box)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await state.emit("agent", {"kind": "say", "text": f"Agent stopped: {type(exc).__name__}: {exc}"})
            await state.emit("run", {"state": "finished", "scenario": body.scenario, "desk": state.env.state()})

        state.run_task = asyncio.create_task(go())
        return {"started": True}

    @app.post("/v1/demo/reset")
    async def demo_reset() -> dict[str, Any]:
        await state.reset()
        return {"ok": True, "index_docs": state.index.doc_count}

    @app.get("/v1/stream")
    async def stream(request: Request) -> StreamingResponse:
        q: asyncio.Queue = asyncio.Queue()
        state.subs.add(q)

        async def gen():
            yield "retry: 1000\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
            finally:
                state.subs.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    ui = ROOT / "ui"
    if ui.exists():
        app.mount("/", NoCacheStatic(directory=ui, html=True), name="ui")
    return app


app = create_app()
