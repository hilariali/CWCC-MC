#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi
export MARKING_DATA_DIR="${MARKING_DATA_DIR:-$(pwd)/data/jobs}"
# Optional shared password: export MARKING_APP_PASSWORD='secret'
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
