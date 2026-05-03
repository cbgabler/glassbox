# GlassBox RAG Server

## Visual test (quick)
```powershell
cd glassbox/backend/ragserver
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe visual_test.py
```

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

---

The server is registered in `agentconfig.yaml` with a **relative path** so any teammate can run the agent after cloning the repo.

**Run the server** (inside the `ragserver` folder):
```powershell
# Windows PowerShell
.
venv\Scripts\activate   # activate virtual env
uvicorn server:app --reload --port 8000
```
Or with the Go agent, which launches the server automatically.

---