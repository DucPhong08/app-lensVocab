#!/usr/bin/env bash
set -euo pipefail

export CI=true

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=".venv/bin/python"
RUFF_BIN=".venv/bin/ruff"

if [[ ! -f "$PYTHON_BIN" ]]; then
    echo "❌ Error: Virtual environment python not found at $PYTHON_BIN. Run 'make install' first." >&2
    exit 1
fi

echo "🔍 [1/2] Running Ruff Linter..."
"$RUFF_BIN" check .

echo "🧪 [2/2] Running Unit & Integration Tests..."
"$PYTHON_BIN" -m unittest discover tests

echo "✅ All QA quality gates passed successfully!"
