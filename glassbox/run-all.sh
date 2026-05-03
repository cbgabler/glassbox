#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# GlassBox one-command local runner (Git Bash / bash shell)
#
# Run from repo root:
#   cd glassbox
#   bash ./run-all.sh
#
# What this script does:
# 1) Backend
#    - Ensures `backend/main.exe` exists (builds via `backend/build-all.sh` if missing)
#    - Starts `backend/main.exe`
#
# 2) Frontend
#    - Ensures `frontend/node_modules` exists (runs `npm install` if missing)
#    - Starts `npm run dev` in `frontend`
#
# 3) RAG server
#    - Ensures `backend/ragserver/venv/Scripts/python.exe` exists
#      (creates venv + installs requirements if missing)
#    - Starts:
#      `./venv/Scripts/python.exe -m uvicorn server:app --host 0.0.0.0 --port 8085`
#
# Shutdown:
# - Press Ctrl+C in this terminal; script trap will kill all spawned processes.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env variables from repo root if present
if [ -f "${SCRIPT_DIR}/../.env" ]; then
  set -o allexport
  source "${SCRIPT_DIR}/../.env"
  set +o allexport
fi

BACKEND_DIR="${SCRIPT_DIR}/backend"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"
RAG_DIR="${BACKEND_DIR}/ragserver"

BACKEND_PID=""
FRONTEND_PID=""
RAG_PID=""

cleanup() {
  echo
  echo "Stopping services..."
  [[ -n "${BACKEND_PID}" ]] && kill "${BACKEND_PID}" 2>/dev/null || true
  [[ -n "${FRONTEND_PID}" ]] && kill "${FRONTEND_PID}" 2>/dev/null || true
  [[ -n "${RAG_PID}" ]] && kill "${RAG_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[1/3] Preparing backend..."
cd "${BACKEND_DIR}"
if [[ ! -f "main.exe" ]]; then
  echo "main.exe not found. Building backend..."
  bash ./build-all.sh
fi

echo "[2/3] Preparing frontend..."
cd "${FRONTEND_DIR}"
if [[ ! -d "node_modules" ]]; then
  npm install
fi

echo "[3/3] Preparing RAG server..."
if [[ ! -d "${RAG_DIR}" ]]; then
  echo "RAG server directory not found at ${RAG_DIR}"
  exit 1
fi
cd "${RAG_DIR}"
if [[ ! -x "venv/Scripts/python.exe" ]]; then
  echo "ragserver venv missing. Creating venv + installing requirements..."
  python -m venv venv
  venv/Scripts/python.exe -m pip install -r requirements.txt
fi

echo
echo "Starting all services..."

cd "${BACKEND_DIR}"
./main.exe &
BACKEND_PID=$!
echo "Backend PID: ${BACKEND_PID}"

cd "${FRONTEND_DIR}"
npm run dev &
FRONTEND_PID=$!
echo "Frontend PID: ${FRONTEND_PID}"

cd "${RAG_DIR}"
venv/Scripts/python.exe -m uvicorn server:app --reload --port 8000 &
RAG_PID=$!
echo "RAG PID: ${RAG_PID}"

echo
echo "Services are up. Press Ctrl+C to stop all."
wait
