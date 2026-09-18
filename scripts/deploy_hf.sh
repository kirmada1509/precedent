#!/usr/bin/env bash
# Publish Precedent to a Hugging Face Docker Space.
#
#   scripts/deploy_hf.sh <hf-username>/<space-name>
#
# Prerequisites (you, once):
#   1. Create the Space at https://huggingface.co/new-space  (SDK: Docker, blank template, public).
#   2. Space -> Settings -> Variables and secrets -> add SECRETS  MOSS_PROJECT_ID  and  MOSS_PROJECT_KEY.
#      Do NOT add GEMINI_API_KEY: a public "Live Gemini agent" button would let anyone spend it.
#   3. Have git credentials for huggingface.co (a write token as the password, or `hf auth login`).
#
# This copies only what the image needs into a temp dir and force-pushes it as the Space's main branch.
set -euo pipefail

SPACE="${1:?usage: scripts/deploy_hf.sh <hf-username>/<space-name>}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/data" "$TMP/docs"
cp -R "$SRC/src" "$SRC/demo" "$SRC/ui" "$TMP/"
cp "$SRC/pyproject.toml" "$SRC/uv.lock" "$SRC/Dockerfile" "$TMP/"
cp "$SRC/data/seed_actions.jsonl" "$SRC/data/calibrated.json" "$TMP/data/"
cp "$SRC/docs/eval.json" "$SRC/docs/bench.json" "$TMP/docs/"
cp "$SRC/hf/README.md" "$TMP/README.md"
find "$TMP" -name __pycache__ -type d -prune -exec rm -rf {} +

# Refuse to publish anything that looks like a credential.
if grep -rIlE '(moss_[0-9a-f]{20,}|AIza[0-9A-Za-z_-]{20,}|sk_[A-Za-z0-9]{20,})' "$TMP" >/dev/null; then
  echo "refusing to push: a credential-like string is in the upload" >&2; exit 1
fi

cd "$TMP"
git init -q -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-precedent}" -c user.email="${GIT_AUTHOR_EMAIL:-precedent@users.noreply.huggingface.co}" \
    commit -q -m "Deploy Precedent"
git push --force "https://huggingface.co/spaces/${SPACE}" main
echo "Pushed. Build logs: https://huggingface.co/spaces/${SPACE}?logs=build"
echo "App:               https://${SPACE/\//-}.hf.space"
