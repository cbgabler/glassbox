# GlassBox

Hackathon project: a local-first AI security auditor that scans repositories, explains findings, and supports hardware-backed side-channel confirmation for C/C++ targets.

## Problem / Benefit
- Security reviews are slow, fragmented, and usually stop at static warnings.
- Teams need faster triage with actionable fixes and clear evidence.
- For side-channel risks, software-only tools cannot prove real hardware leakage.

## Solution (Our Project)
GlassBox combines automated scanners, an AI audit assistant, and hardware verification into one workflow.

## Hardware USP (Our Differentiator)
- GlassBox goes beyond static analysis by validating side-channel risk on real hardware.
- Our pod combines an ESP32 target with a Pico-based control/capture path for deterministic timing/power checks.
- For eligible C/C++ findings, this provides measurable evidence of leakage on silicon, not just “pattern matched” warnings.
- Outcome: higher-confidence triage, fewer false positives, and a clear detect-plus-verify story for security teams.

## What It Does / How It Works
- Ingests a local repo path or git URL.
- Runs security checks (side-channel patterns, secrets, endpoint risks, dependency risk, and context search).
- Streams findings into the UI with severity and fix guidance.
- Lets users ask natural-language audit questions via chat.
- For C/C++ paths, can register/scan hardware-compatible targets for confirmation workflows.

## Tech Stack
- Backend: Go (MCP-style tool servers + orchestrator)
- RAG server: Python + FastAPI + FAISS
- Frontend: React + TypeScript + Vite
- LLM: OpenAI-compatible provider (configured by key/model)

## Tools Used
- Go MCP tooling (`GOMCP`)
- FastAPI, Uvicorn
- FAISS, Sentence Transformers
- React, Vite, Tailwind ecosystem
- Optional search integrations (Google/Tavily connectors)

## Workflow Diagram
```mermaid
flowchart LR
  U[User] --> F[Frontend UI]
  F --> B[Go Backend Agent]
  B --> S1[Repo Context Server]
  B --> S2[Search Servers]
  B --> S3[Hardware Server]
  B --> R[RAG Server (FastAPI + FAISS)]
  B --> LLM[LLM API]
  S3 --> HW[(Hardware Pod - optional)]
```

## How To Run
Run in separate terminals.

### 1) Backend
```bash
cd glassbox/backend
./build-all.sh
./main.exe
```

### 2) Frontend
```bash
cd glassbox/frontend
npm install
npm run dev
```

### 3) RAG Server (if running standalone)
```bash
cd glassbox/backend/ragserver
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn server:app --reload --port 8000
```

Note: In some flows, backend config can launch RAG-related services automatically.

## Required API Keys
Set these in `.env` files (do not hardcode in source):

- Backend (`glassbox/backend/.env`)
  - `OPENAI_API_KEY` (required for LLM calls)
  - `NVIDIA_API_KEY` (required for NVIDIA embedding path in RAG, if used)
  - `GOOGLE_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` (optional)
  - `TAVILY_API_KEY` (optional)

- Frontend (`glassbox/frontend/.env`)
  - `VITE_API_URL` (default: `http://localhost:8080`)
  - `VITE_WS_URL` (default: `ws://localhost:8080/ws`)
