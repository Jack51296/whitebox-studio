#!/usr/bin/env bash
# Local / server CI: lint, schema drift check, tests, optional mock end-to-end run.
#   scripts/ci.sh            lint + all tests (Blender/ffmpeg tests skip when tools are absent)
#   scripts/ci.sh --fast     skip tests that need Blender
#   scripts/ci.sh --e2e      also run scripts/e2e_mock.py --quick
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONUTF8=1
unset WBS_CONFIRM_PAID || true

"$PY" -m ruff check src tests scripts
"$PY" scripts/export_schemas.py > /dev/null
git diff --exit-code -- schemas || { echo "schemas/ is stale: commit the regenerated files"; exit 1; }

if [[ " $* " == *" --fast "* ]]; then
  "$PY" -m pytest -q -m "not blender"
else
  "$PY" -m pytest -q
fi

if [[ " $* " == *" --e2e "* ]]; then
  "$PY" scripts/e2e_mock.py --quick
fi
echo "CI OK"
