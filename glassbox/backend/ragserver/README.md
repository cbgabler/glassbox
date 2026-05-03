# GlassBox RAG Server

## What is it?

The **RAG server** is a lightweight Python side‑car that provides Retrieval‑Augmented Generation (RAG) capabilities for the GlassBox security agent.  It stores audit findings and code snippets in a local **FAISS** vector database, allowing the main Claude‑based agent to retrieve relevant information quickly during a scan.

## Purpose
- Persistently index code and vulnerability findings.
- Offer semantic search via a FastAPI MCP tool server.
- Bridge the main Go agent with high‑quality embeddings (NVIDIA Nemotron for findings, local Sentence‑Transformers for code).

## What this enables for the agent
- Index findings semantically so retrieval works by meaning, not just keywords.
- Search findings by intent and return the best matches with severity and advice.
- Store and retrieve code snippets with file/line context for natural-language queries.
- Keep findings and code in the same run id for cross-referencing issues to code.
- Persist indexes so the agent can answer without re-indexing every run.
- Support fuzzy, semantic queries even when wording differs.
- Run multiple searches in one turn for richer answers.

## Tech Stack
- **Python 3.8+**
- **FAISS‑CPU** (`faiss-cpu`)
- **FastAPI** + **Uvicorn** (HTTP MCP server)
- **Sentence‑Transformers** (`all‑MiniLM‑L6‑v2`) for local code embeddings
- **OpenAI‑compatible async client** for NVIDIA Nemotron embeddings
- **Pydantic** for request/response models
- **dotenv** for secret management

## File Overview
| File | Description |
|------|-------------|
| `embedder.py` | Wraps two embedder classes: `FindingsEmbedder` (calls NVIDIA Nemotron via OpenAI API) and `CodeEmbedder` (local Sentence‑Transformers model). |
| `store.py` | Core FAISS wrapper (`RAGStore`). Handles chunking, .gitignore‑aware indexing, persisting indexes, and provides `add_finding`, `search_findings`, `search_code`. |
| `models.py` | Pydantic schemas for findings, severity, and MCP request/response payloads. |
| `server.py` | FastAPI app exposing MCP endpoints (`/execute/add_finding`, `/execute/search_findings`, `/execute/search_code`). |
| `visual_test.py` | Small end‑to‑end demo script: indexes a mock finding (or real one if `NVIDIA_API_KEY` is set) and runs a semantic query to showcase the RAG pipeline. |
| `visualizer/` | Standalone Dash + Plotly visualizer for embeddings (local-only UI). |

---

The server is registered in `agentconfig.yaml` with a **relative path** so any teammate can run the agent after cloning the repo.

**Run the server** (inside the `ragserver` folder):
```powershell
# Windows PowerShell
cd glassbox/backend/ragserver
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8085
```

```bash
# Git Bash
cd glassbox/backend/ragserver
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m uvicorn server:app --host 0.0.0.0 --port 8085
```

Or with the Go agent, which launches the server automatically.

---

## Visual test (quick)
```powershell
cd glassbox/backend/ragserver
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe visual_test.py
```

## Visualizer (local Dash UI)
```powershell
cd glassbox/backend/ragserver
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe visualizer\plotly_dash_viewer.py --run-id visual_test_run
```

## Bash helpers (mock data + visualizer)
```bash
cd glassbox/backend/ragserver

# 1) Seed mock findings + code chunks into FAISS for a run_id
bash ./seed_mock_data.sh visual_test_run

# 2) Launch the visualizer in 3D
bash ./run_visualizer.sh visual_test_run
```

Optional environment variables:
- `RAG_SERVER_URL` (default `http://localhost:8000`)
- `DIMS` (default `3`)
- `REDUCE_METHOD` (default `umap`)
- `PORT` (default `8050`)
