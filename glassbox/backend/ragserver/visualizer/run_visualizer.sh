#!/usr/bin/env bash
set -euo pipefail

RUN_ID="${1:-visual_test_run}"
RAG_SERVER_URL="${RAG_SERVER_URL:-http://localhost:8000}"
DIMS="${DIMS:-3}"
REDUCE_METHOD="${REDUCE_METHOD:-umap}"
PORT="${PORT:-8050}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN="${SCRIPT_DIR}/venv/Scripts/python.exe"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/.venv/Scripts/python.exe"
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Could not find python in venv/.venv under ${SCRIPT_DIR}" >&2
  exit 1
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/visualizer/plotly_dash_viewer.py" \
  --run-id "${RUN_ID}" \
  --server-url "${RAG_SERVER_URL}" \
  --dims "${DIMS}" \
  --reduce-method "${REDUCE_METHOD}" \
  --port "${PORT}"
