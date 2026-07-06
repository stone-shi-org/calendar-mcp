#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

PYTHON="$SCRIPT_DIR/venv/bin/python"

PIP_ARGS="--no-cache-dir -q"
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$PYTHON" -m pip install $PIP_ARGS -r "$SCRIPT_DIR/requirements.txt"
fi
"$PYTHON" -m pip install $PIP_ARGS pytest pytest-asyncio

exec "$PYTHON" -m pytest --junitxml=test-reports/results.xml "$SCRIPT_DIR/tests" -v "$@"
