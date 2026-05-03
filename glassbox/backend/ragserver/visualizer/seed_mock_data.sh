#!/usr/bin/env bash
set -euo pipefail

RUN_ID="${1:-visual_test_run}"
RAG_SERVER_URL="${RAG_SERVER_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_REPO_DIR="${SCRIPT_DIR}/mock_repo"

echo "[seed] run_id=${RUN_ID}"
echo "[seed] server=${RAG_SERVER_URL}"

mkdir -p "${MOCK_REPO_DIR}/src"

cat > "${MOCK_REPO_DIR}/src/auth.ts" <<'EOF'
export function login() {
  const API_KEY = "sk_test_123456";
  return API_KEY.length > 0;
}
EOF

cat > "${MOCK_REPO_DIR}/src/server.ts" <<'EOF'
import express from "express";
const app = express();
app.get("/debug", (req, res) => res.send("ok"));
app.listen(3000);
EOF

cat > "${MOCK_REPO_DIR}/src/crypto.c" <<'EOF'
#include <string.h>
int check_password(const char *user, const char *expected) {
  return strcmp(user, expected) == 0;
}
EOF

echo "[seed] adding mock findings..."
curl -sS -X POST "${RAG_SERVER_URL}/execute/add_finding" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "run_id": "${RUN_ID}",
  "finding": {
    "id": "F-001",
    "scanner": "secrets",
    "severity": "HIGH",
    "title": "Hardcoded API key",
    "description": "An API key appears directly in source code.",
    "file": "mock_repo/src/auth.ts",
    "line": 2,
    "snippet": "const API_KEY = \"sk_test_123456\";",
    "advice": "Move secrets to environment variables or a secret manager.",
    "can_hw_confirm": false
  }
}
EOF
echo

curl -sS -X POST "${RAG_SERVER_URL}/execute/add_finding" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "run_id": "${RUN_ID}",
  "finding": {
    "id": "F-002",
    "scanner": "endpoints",
    "severity": "MEDIUM",
    "title": "Unauthenticated debug endpoint",
    "description": "Debug endpoint can be called without auth middleware.",
    "file": "mock_repo/src/server.ts",
    "line": 3,
    "snippet": "app.get(\"/debug\", (req, res) => res.send(\"ok\"));",
    "advice": "Protect debug routes with authentication and restrict exposure.",
    "can_hw_confirm": false
  }
}
EOF
echo

echo "[seed] indexing mock code snippets from ${MOCK_REPO_DIR}..."
curl -sS -X POST "${RAG_SERVER_URL}/execute/index_code" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "run_id": "${RUN_ID}",
  "repo_path": "${MOCK_REPO_DIR}"
}
EOF
echo

echo "[seed] done. You can now open the visualizer for run_id=${RUN_ID}."
