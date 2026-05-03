import os
import faiss
import numpy as np
import pickle
import pathspec
from pathlib import Path
from typing import List, Dict, Optional
from .models import Finding, Severity
from .embedder import FindingsEmbedder, CodeEmbedder

class RAGStore:
    def __init__(self, run_id: str, findings_embedder: FindingsEmbedder, code_embedder: CodeEmbedder):
        self.run_id = run_id
        self.findings_embedder = findings_embedder
        self.code_embedder = code_embedder
        
        # Two separate indexes
        self.findings_index: Optional[faiss.Index] = None
        self.findings_metadata: List[Finding] = []
        
        self.code_index: Optional[faiss.Index] = None
        self.code_metadata: List[Dict] = []  # {file, start_line, end_line, content}
        
        self.storage_path = Path.home() / ".glassbox" / "runs" / run_id
        self.storage_path.mkdir(parents=True, exist_ok=True)

    async def add_finding(self, finding: Finding):
        """Index a single finding."""
        text = finding.to_embed_text()
        vector = await self.findings_embedder.embed(text)
        
        if self.findings_index is None:
            self.findings_index = faiss.IndexFlatL2(vector.shape[1])
            
        self.findings_index.add(vector)
        self.findings_metadata.append(finding)

    async def index_code_repo(self, repo_path: str):
        """Crawl repo, respect .gitignore, chunk, and embed."""
        root = Path(repo_path)
        if not root.exists():
            raise ValueError(f"Repo path {repo_path} does not exist.")

        # Load .gitignore if it exists
        gitignore_path = root / ".gitignore"
        spec = None
        if gitignore_path.exists():
            with open(gitignore_path, "r", encoding="utf-8") as f:
                spec = pathspec.PathSpec.from_lines("gitwildmatch", f)

        # Standard extensions to index
        valid_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".c", ".cpp", ".h", ".go", ".rs", ".java"}

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            
            # Check .gitignore
            relative_path = path.relative_to(root)
            if spec and spec.match_file(str(relative_path)):
                continue
                
            if path.suffix not in valid_extensions:
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    await self._index_file_content(str(relative_path), content)
            except Exception as e:
                print(f"Error reading {path}: {e}")

    async def _index_file_content(self, relative_path: str, content: str):
        """Chunk a single file and add to code index."""
        lines = content.splitlines()
        chunk_size = 50
        overlap = 10
        
        for i in range(0, len(lines), chunk_size - overlap):
            chunk_lines = lines[i : i + chunk_size]
            chunk_text = "\n".join(chunk_lines)
            if not chunk_text.strip():
                continue
                
            vector = await self.code_embedder.embed(f"File: {relative_path}\n{chunk_text}")
            
            if self.code_index is None:
                self.code_index = faiss.IndexFlatL2(vector.shape[1])
                
            self.code_index.add(vector)
            self.code_metadata.append({
                "file": relative_path,
                "start_line": i + 1,
                "end_line": i + len(chunk_lines),
                "content": chunk_text
            })

    async def search_findings(self, query: str, k: int = 5, severity: Optional[Severity] = None, scanner: Optional[str] = None) -> List[Finding]:
        if self.findings_index is None or not self.findings_metadata:
            return []
            
        vector = await self.findings_embedder.embed(query)
        # Search for more than k to allow for filtering
        search_k = k * 5
        distances, indices = self.findings_index.search(vector, search_k)
        
        results = []
        for idx in indices[0]:
            if idx < 0 or idx >= len(self.findings_metadata):
                continue
            finding = self.findings_metadata[idx]
            
            # Apply filters
            if severity and finding.severity != severity:
                continue
            if scanner and finding.scanner != scanner:
                continue
                
            results.append(finding)
            if len(results) >= k:
                break
        return results

    async def search_code(self, query: str, k: int = 5) -> List[Dict]:
        if self.code_index is None or not self.code_metadata:
            return []
            
        vector = await self.code_embedder.embed(query)
        distances, indices = self.code_index.search(vector, k)
        
        results = []
        for idx in indices[0]:
            if idx < 0 or idx >= len(self.code_metadata):
                continue
            results.append(self.code_metadata[idx])
        return results

    def save(self):
        """Persist indexes and metadata to disk."""
        if self.findings_index:
            faiss.write_index(self.findings_index, str(self.storage_path / "findings.index"))
        with open(self.storage_path / "findings_meta.pkl", "wb") as f:
            pickle.dump(self.findings_metadata, f)
            
        if self.code_index:
            faiss.write_index(self.code_index, str(self.storage_path / "code.index"))
        with open(self.storage_path / "code_meta.pkl", "wb") as f:
            pickle.dump(self.code_metadata, f)

    def load(self):
        """Load indexes and metadata from disk."""
        if (self.storage_path / "findings.index").exists():
            self.findings_index = faiss.read_index(str(self.storage_path / "findings.index"))
        if (self.storage_path / "findings_meta.pkl").exists():
            with open(self.storage_path / "findings_meta.pkl", "rb") as f:
                self.findings_metadata = pickle.load(f)
                
        if (self.storage_path / "code.index").exists():
            self.code_index = faiss.read_index(str(self.storage_path / "code.index"))
        if (self.storage_path / "code_meta.pkl").exists():
            with open(self.storage_path / "code_meta.pkl", "rb") as f:
                self.code_metadata = pickle.load(f)
