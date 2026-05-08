# GlassBox 🏆

1st Place Beaverhacks Hackathon project: a local-first AI security auditor that scans repositories, explains findings, and supports hardware-backed side-channel confirmation for C/C++ targets.

## Problem / Benefit
- Security reviews are slow, fragmented, and usually stop at static warnings.
- Teams need faster triage with actionable fixes and clear evidence.
- For side-channel risks, software-only tools cannot prove real hardware leakage.

## Solution (Our Project)
GlassBox combines automated scanners, an AI audit assistant, and hardware verification into one workflow.

## Agent Infrastructure
A Go-based backend orchestrates an MCP-connected agent that handles the full audit loop: repo cloning, context loading, tool/scanner execution, and response assembly.

It analyzes for:
- side-channel leak patterns
- exposed secrets and tokens
- risky endpoints and auth gaps
- dependency vulnerabilities

Findings are surfaced in plain language with repository context. If something is unclear, users can ask follow-up questions directly in chat.

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
- RAG server: Python + FastAPI + FAISS (nv-embedqa-e5-v5 embedding model)
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
Use PowerShell. Run in separate terminals.

### 0) One-time setup (RAG dependencies)
```powershell
cd glassbox\backend\ragserver
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `glassbox/backend/.env` with at least:
```env
OPENAI_API_KEY=your_key_here
# Optional:
# NVIDIA_API_KEY=your_key_here
# GOOGLE_API_KEY=your_key_here
# GOOGLE_SEARCH_ENGINE_ID=your_search_engine_id
# TAVILY_API_KEY=your_key_here
```

Optional key usage:
- `NVIDIA_API_KEY`: Enables NVIDIA-backed findings embeddings in the RAG server.
- `GOOGLE_API_KEY`: Enables Google web search tool calls.
- `GOOGLE_SEARCH_ENGINE_ID`: Custom Search Engine ID paired with `GOOGLE_API_KEY`.
- `TAVILY_API_KEY`: Enables Tavily web search tool calls.

### 1) Backend
```powershell
cd glassbox\backend
.\build-all.ps1
.\main.exe
```

### 2) Frontend
```powershell
cd glassbox\frontend
npm install
npm run dev
```

### 3) RAG Server (if running standalone)
```powershell
cd glassbox\backend\ragserver
.\.venv\Scripts\python.exe -m uvicorn server:app --reload --port 8000
```

Note: In some flows, backend config can launch RAG-related services automatically.

## Required API Keys
Set these in `.env` files (do not hardcode in source):

- Backend (`glassbox/backend/.env`)
  - `OPENAI_API_KEY` (required for LLM calls)
  - `NVIDIA_API_KEY` (required for NVIDIA embedding path in RAG, if used)
  - `GOOGLE_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` (optional)
  - `TAVILY_API_KEY` (optional)

- Frontend
  - No required `.env` for default local run.
  - Frontend currently targets `http://localhost:8080` and `ws://localhost:8080/ws`.
