import json
from pathlib import Path

import pytest

from precedent.guard import Guard
from precedent.index import MemorySession, PrecedentIndex, load_jsonl

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def index():
    idx = PrecedentIndex(MemorySession(), alpha=0.5, top_k=8)
    await idx.seed(load_jsonl(ROOT / "data" / "seed_actions.jsonl"))
    return idx


@pytest.fixture
def guard(index):
    return Guard(index, budget_ms=500)
