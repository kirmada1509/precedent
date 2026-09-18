# Moss's native core ships a manylinux_2_35 wheel: needs glibc >= 2.35, so Debian bookworm (2.36).
# NOTE: build for linux/amd64. The aarch64 wheel needs glibc 2.39, which bookworm does not have.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Hugging Face Spaces runs containers as uid 1000; Railway and others don't care. Use it everywhere.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user
WORKDIR /home/user/app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Dependencies first (cached layer). The project is run from source, not installed, so path
# resolution (data/, ui/, docs/) stays relative to the app directory.
COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=user src ./src
COPY --chown=user demo ./demo
COPY --chown=user ui ./ui
COPY --chown=user data/seed_actions.jsonl data/calibrated.json ./data/
COPY --chown=user docs/eval.json docs/bench.json ./docs/

ENV PATH="/home/user/app/.venv/bin:$PATH" PYTHONPATH="/home/user/app/src:/home/user/app" \
    PRECEDENT_DATA_DIR=/home/user/app/state PORT=7860 \
    PRECEDENT_BUDGET_MS=100
RUN mkdir -p /home/user/app/state

EXPOSE 7860
CMD ["sh", "-c", "uvicorn precedent.api:app --host 0.0.0.0 --port ${PORT}"]
