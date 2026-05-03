"""
GlassBox RAG embedding setup
============================

This module provides the two embedders used by the RAG sidecar and FAISS store:

1) FindingsEmbedder (remote, NVIDIA)
   - Uses NVIDIA's OpenAI-compatible API endpoint via `AsyncOpenAI`.
   - Default model: `nvidia/nv-embedqa-e5-v5`.
   - Input is vulnerability finding text (title/description/evidence context).
   - This gives higher-quality semantic retrieval for security findings.

2) CodeEmbedder (local, Sentence-Transformers)
   - Uses local model `all-MiniLM-L6-v2` for source code chunks.
   - Runs on the local machine and downloads once on first use.
   - Keeps code indexing/search usable even when remote embedding is limited.

How this relates to FAISS
-------------------------
- `store.py` calls these embedders to produce float vectors.
- Those vectors are inserted into FAISS indexes:
  - one index for findings
  - one index for code chunks
- Query embedding + nearest-neighbor lookup powers `search_findings` and
  `search_code` in the RAG server.

Important environment variables
-------------------------------
- `NVIDIA_API_KEY` (required for FindingsEmbedder)
- `NEMOTRON_BASE_URL` (default: `https://integrate.api.nvidia.com/v1`)
- `NEMOTRON_EMBEDDING_MODEL` (default: `nvidia/nv-embedqa-e5-v5`)
"""

import os
import asyncio
import numpy as np
from typing import List
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# Load .env from ragserver directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

class FindingsEmbedder:
    """Calls NVIDIA nv-embedqa-e5-v5 via OpenAI-compatible API."""
    def __init__(self):
        self.api_key = os.getenv("NVIDIA_API_KEY")
        self.base_url = os.getenv("NEMOTRON_BASE_URL", "https://integrate.api.nvidia.com/v1")
        self.model = os.getenv("NEMOTRON_EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")
        
        if not self.api_key:
            print("Warning: NVIDIA_API_KEY not set. Findings embedding will fail.")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def embed(self, text: str) -> np.ndarray:
        if not self.api_key:
            raise ValueError("NVIDIA_API_KEY is required for findings embedding.")
            
        response = await self.client.embeddings.create(
            input=[text],
            model=self.model,
            encoding_format="float",
            extra_body={"input_type": "query"}
        )
        return np.array([response.data[0].embedding], dtype=np.float32)

class CodeEmbedder:
    """Runs all-MiniLM-L6-v2 locally via sentence-transformers."""
    def __init__(self):
        print("Initializing local code embedder (all-MiniLM-L6-v2)...")
        # This will download the model on first run
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self._lock = asyncio.Lock()

    async def embed(self, text: str) -> np.ndarray:
        # SentenceTransformer.encode is sync, wrap in thread for async safety if needed, 
        # but here we just lock to prevent concurrent model usage if it's not thread-safe.
        async with self._lock:
            # encoded will be a numpy array
            embedding = self.model.encode([text], convert_to_numpy=True)
            return embedding.astype(np.float32)
