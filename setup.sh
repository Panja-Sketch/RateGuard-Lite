#!/usr/bin/env bash
# RateGuard-Lite setup for macOS / Linux. No admin rights needed.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found. Install it (python.org or your package manager) and re-run ./setup.sh" >&2
  exit 1
fi
echo "Using $($PY --version)"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
[ -f requirements.lock ] && .venv/bin/python -m pip install --quiet -r requirements.lock
.venv/bin/python -m pip install --quiet -e ".[dev]"

echo "Running self-test..."
.venv/bin/rgl selftest
echo
echo "Ready. Activate with:  source .venv/bin/activate   then try:  rgl --help"
