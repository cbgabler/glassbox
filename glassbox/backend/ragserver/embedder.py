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
