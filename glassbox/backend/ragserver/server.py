import os
import asyncio
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException, Body
from .models import Finding, Severity, SearchRequest, IndexCodeRequest, AddFindingRequest
from .embedder import FindingsEmbedder, CodeEmbedder
from .store import RAGStore

app = FastAPI(title="GlassBox RAG MCP Server")

# Global state
findings_embedder = FindingsEmbedder()
code_embedder = CodeEmbedder()
stores: Dict[str, RAGStore] = {}

def get_store(run_id: str) -> RAGStore:
    if run_id not in stores:
        stores[run_id] = RAGStore(run_id, findings_embedder, code_embedder)
        # Try to load existing index if it exists
        stores[run_id].load()
    return stores[run_id]

@app.get("/health")
async def health():
    return {"status": "ok", "run_ids": list(stores.keys())}

@app.post("/execute/add_finding")
async def add_finding(request: AddFindingRequest):
    store = get_store(request.run_id)
    await store.add_finding(request.finding)
    store.save()  # Auto-save for safety in hackathon build
    return {"ok": True}

@app.post("/execute/index_code")
async def index_code(request: IndexCodeRequest):
    store = get_store(request.run_id)
    await store.index_code_repo(request.repo_path)
    store.save()
    return {"ok": True, "message": f"Indexed repository at {request.repo_path}"}

@app.post("/execute/search_findings")
async def search_findings(request: SearchRequest):
    store = get_store(request.run_id)
    results = await store.search_findings(
        query=request.query, 
        k=request.k, 
        severity=request.severity_filter, 
        scanner=request.scanner_filter
    )
    return {"results": [r.model_dump() for r in results]}

@app.post("/execute/search_code")
async def search_code(request: SearchRequest):
    store = get_store(request.run_id)
    results = await store.search_code(query=request.query, k=request.k)
    return {"results": results}

@app.post("/execute/save_index")
async def save_index(run_id: str = Body(..., embed=True)):
    if run_id in stores:
        stores[run_id].save()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Run ID not found")

@app.post("/execute/load_index")
async def load_index(run_id: str = Body(..., embed=True)):
    store = get_store(run_id)
    store.load()
    return {"ok": True, "findings_count": len(store.findings_metadata), "code_chunks_count": len(store.code_metadata)}
