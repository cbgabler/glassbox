import os
import asyncio
import uuid
from typing import Dict, Optional, Any
from fastapi import FastAPI, HTTPException, Body
from models import (
    Finding,
    Severity,
    SearchRequest,
    IndexCodeRequest,
    AddMemoryNoteRequest,
    SearchMemoryRequest,
)
from embedder import FindingsEmbedder, CodeEmbedder
from store import RAGStore

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

def _normalize_finding_payload(payload: Dict[str, Any]) -> tuple[str, Finding]:
    run_id = payload.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    raw_finding = payload.get("finding") if isinstance(payload.get("finding"), dict) else payload

    severity_value = str(raw_finding.get("severity", "LOW")).upper()
    try:
        severity = Severity(severity_value)
    except ValueError:
        severity = Severity.LOW

    finding = Finding(
        id=str(raw_finding.get("id") or raw_finding.get("finding_id") or uuid.uuid4()),
        scanner=str(raw_finding.get("scanner") or raw_finding.get("category") or "unknown"),
        severity=severity,
        title=str(raw_finding.get("title") or raw_finding.get("name") or "Untitled finding"),
        description=str(raw_finding.get("description") or raw_finding.get("details") or ""),
        file=raw_finding.get("file"),
        line=raw_finding.get("line"),
        snippet=raw_finding.get("snippet"),
        advice=str(raw_finding.get("advice") or raw_finding.get("fix") or raw_finding.get("remediation") or ""),
        can_hw_confirm=bool(raw_finding.get("can_hw_confirm", False)),
    )
    return run_id, finding


@app.post("/execute/add_finding")
async def add_finding(payload: Dict[str, Any] = Body(...)):
    run_id, finding = _normalize_finding_payload(payload)
    store = get_store(run_id)
    await store.add_finding(finding)
    store.save()  # Auto-save for safety in hackathon build
    return {"ok": True}

@app.post("/execute/index_code")
async def index_code(request: IndexCodeRequest):
    store = get_store(request.run_id)
    # Re-index from scratch for deterministic behavior per run_id.
    store.code_index = None
    store.code_metadata = []
    await store.index_code_repo(request.repo_path)
    store.save()
    return {
        "ok": True,
        "message": f"Indexed repository at {request.repo_path}",
        "run_id": request.run_id,
        "code_chunks_count": len(store.code_metadata),
    }

@app.post("/execute/search_findings")
async def search_findings(request: SearchRequest):
    store = get_store(request.run_id)
    if not store.findings_metadata:
        return {
            "results": [],
            "warning": "No findings indexed for this run_id yet. Call add_finding first.",
            "run_id": request.run_id,
        }
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
    if not store.code_metadata:
        return {
            "results": [],
            "warning": "No code has been indexed for this run_id yet. Call index_code with repo_path before search_code.",
            "run_id": request.run_id,
        }
    results = await store.search_code(query=request.query, k=request.k)
    return {"results": results}

@app.post("/execute/add_memory_note")
async def add_memory_note(request: AddMemoryNoteRequest):
    store = get_store(request.run_id)
    await store.add_memory_note(request.note)
    store.save()
    return {
        "ok": True,
        "run_id": request.run_id,
        "memory_notes_count": len(store.memory_metadata),
    }

@app.post("/execute/search_memory")
async def search_memory(request: SearchMemoryRequest):
    store = get_store(request.run_id)
    if not store.memory_metadata:
        return {
            "results": [],
            "warning": "No memory notes stored for this run_id yet. Add notes with add_memory_note at the end of workflows.",
            "run_id": request.run_id,
        }
    results = await store.search_memory_notes(query=request.query, k=request.k, tag_filter=request.tag_filter)
    return {"results": [r.model_dump() for r in results]}

@app.post("/execute/index_status")
async def index_status(run_id: str = Body(..., embed=True)):
    store = get_store(run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "findings_count": len(store.findings_metadata),
        "code_chunks_count": len(store.code_metadata),
        "memory_notes_count": len(store.memory_metadata),
        "code_indexed": len(store.code_metadata) > 0,
        "findings_indexed": len(store.findings_metadata) > 0,
        "memory_indexed": len(store.memory_metadata) > 0,
    }

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
    return {
        "ok": True,
        "findings_count": len(store.findings_metadata),
        "code_chunks_count": len(store.code_metadata),
        "memory_notes_count": len(store.memory_metadata),
    }
