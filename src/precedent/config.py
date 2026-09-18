"""Runtime configuration from the environment (and a local .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader; existing environment variables win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    moss_project_id: str
    moss_project_key: str
    index_name: str
    alpha: float
    top_k: int
    budget_ms: float  # hard per-check budget; exceeded -> fail closed
    data_dir: Path
    gemini_api_key: str
    gemini_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            moss_project_id=os.getenv("MOSS_PROJECT_ID", ""),
            moss_project_key=os.getenv("MOSS_PROJECT_KEY", ""),
            index_name=os.getenv("PRECEDENT_INDEX_NAME", "precedent-live"),
            alpha=float(os.getenv("PRECEDENT_ALPHA", "0.5")),
            top_k=int(os.getenv("PRECEDENT_TOP_K", "8")),
            budget_ms=float(os.getenv("PRECEDENT_BUDGET_MS", "50")),
            data_dir=Path(os.getenv("PRECEDENT_DATA_DIR", str(ROOT / "data"))),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        )
