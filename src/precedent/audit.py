"""Append-only JSONL audit trail plus the human-adjudication ledger that is replayed on boot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Audit:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.decisions = self.dir / "audit.jsonl"
        self.adjudications = self.dir / "adjudications.jsonl"

    def _append(self, path: Path, obj: dict[str, Any]) -> None:
        with path.open("a") as f:
            f.write(json.dumps(obj, default=str) + "\n")

    def log_decision(self, card: dict[str, Any]) -> None:
        self._append(self.decisions, card)

    def log_adjudication(self, record: dict[str, Any]) -> None:
        self._append(self.adjudications, record)

    def adjudication_records(self) -> list[dict[str, Any]]:
        if not self.adjudications.exists():
            return []
        return [json.loads(x) for x in self.adjudications.read_text().splitlines() if x.strip()]

    def reset_adjudications(self) -> None:
        self.adjudications.unlink(missing_ok=True)
