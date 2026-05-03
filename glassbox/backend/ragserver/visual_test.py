"""
1) Initiliaze FindingsEmbedder with Nemotron
2) Index a Finding using Nemotron
3) Query for the finding
4) Print the results
5) Close the async OpenAI client
"""

import asyncio
import os
from dotenv import load_dotenv
from store import RAGStore
from embedder import FindingsEmbedder, CodeEmbedder
from models import Finding, Severity

# Load the .env file (it's 3 levels up from this folder)
dotenv_path = os.path.join(os.path.dirname(__file__), "../../../.env")
load_dotenv(dotenv_path)

async def run_visual_test():
    print("\n" + "="*50)
    print(" 🧠 GLASSBOX RAG TEST (NVIDIA NEMOTRON)")
    print("="*50)
    
    # Initialize real embedders
    findings_embedder = FindingsEmbedder()  # Uses NVIDIA
    code_embedder = CodeEmbedder()         # Uses Local model
    
    store = RAGStore("visual_test_run", findings_embedder, code_embedder)
    
    # 1. Index a Finding using Nemotron
    print("\n[1/3] Indexing Finding via NVIDIA Nemotron...")
    if not os.getenv("NVIDIA_API_KEY") or "xxx" in os.getenv("NVIDIA_API_KEY"):
        print("  ⚠️ NVIDIA_API_KEY not found in .env. Skipping real API call and using Mock Findings.")
    else:
        finding = Finding(
            id="f1",
            scanner="secrets",
            severity=Severity.CRITICAL,
            title="Plaintext AWS Key in config.py",
            description="Found a hardcoded AWS_SECRET_KEY which allows full S3 access.",
            file="config.py",
            advice="Move secrets to environment variables."
        )
        await store.add_finding(finding)
        print("  ✅ Finding Indexed successfully.")
    
    # 2. Semantic Query
    query = "Show me high-risk cloud credential leaks"
    print(f"\n[2/3] Querying (Semantic Search): \"{query}\"")
    results = await store.search_findings(query, k=1)
    
    # 3. Results
    print("\n[3/3] Result from Vector DB:")
    if results:
        res = results[0]
        print(f"  📍 Title: {res.title}")
        print(f"  📊 Severity: {res.severity}")
        print(f"  💡 Advice: {res.advice}")
    else:
        print("  ❌ No results found. (Check your NVIDIA_API_KEY)")
    print("-" * 50)
    
    # ---- Cleanup ----
    # Close the async OpenAI client safely inside the running loop
    if hasattr(findings_embedder, "client") and findings_embedder.client:
        try:
            await findings_embedder.client.aclose()
        except Exception:
            pass

    print("="*50)
    
    # Initialize real embedders
    findings_embedder = FindingsEmbedder() # Uses NVIDIA
    code_embedder = CodeEmbedder()         # Uses Local model
    
    store = RAGStore("visual_test_run", findings_embedder, code_embedder)
    
    # 1. Index a Finding using Nemotron
    print("\n[1/3] Indexing Finding via NVIDIA Nemotron...")
    
    if not os.getenv("NVIDIA_API_KEY") or "xxx" in os.getenv("NVIDIA_API_KEY"):
        print("  ⚠️ NVIDIA_API_KEY not found in .env. Skipping real API call and using Mock Findings.")
    else:
        finding = Finding(
            id="f1",
            scanner="secrets",
            severity=Severity.CRITICAL,
            title="Plaintext AWS Key in config.py",
            description="Found a hardcoded AWS_SECRET_KEY which allows full S3 access.",
            file="config.py",
            advice="Move secrets to environment variables."
        )
        await store.add_finding(finding)
        print("  ✅ Finding Indexed successfully.")

    # 2. Semantic Query
    query = "Show me high-risk cloud credential leaks"
    print(f"\n[2/3] Querying (Semantic Search): \"{query}\"")
    
    results = await store.search_findings(query, k=1)

    # 3. Results
    print(f"\n[3/3] Result from Vector DB:")
    if results:
        res = results[0]
        print(f"  📍 Title: {res.title}")
        print(f"  📊 Severity: {res.severity}")
        print(f"  💡 Advice: {res.advice}")
    else:
        print("  ❌ No results found. (Check your NVIDIA_API_KEY)")
    print("-" * 50)

    # ---- Cleanup ----
    # Close the async OpenAI client if it was created to avoid the
    # "Event loop is closed" warning on Windows.
    if hasattr(findings_embedder, "client") and findings_embedder.client:
        # AsyncOpenAI uses httpx; aclose() safely shuts down the transport.
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(findings_embedder.client.aclose())
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(run_visual_test())
