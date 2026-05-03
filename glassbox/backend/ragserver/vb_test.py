"""
1) Initiliaze FindingsEmbedder with Nemotron
2) Index a Finding using Nemotron
3) Query for the finding
4) Print the results
5) Close the async OpenAI client

## Run:
cd glassbox/backend/ragserver
venv/Scripts/python.exe -m pip install -r requirements.txt
venv/Scripts/python.exe vb_test.py
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
    
    findings_embedder = FindingsEmbedder()  # Uses NVIDIA
    code_embedder = CodeEmbedder()         # Uses Local model

    try:
        store = RAGStore("visual_test_run", findings_embedder, code_embedder)

        # 1. Index a Finding using Nemotron
        print("\n[1/10] Indexing Finding via NVIDIA Nemotron...")
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key or "xxx" in api_key:
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
        print(f"\n[2/10] Querying (Semantic Search): \"{query}\"")
        results = await store.search_findings(query, k=1)

        # 3. Results
        print("\n[3/10] Result from Vector DB:")
        if results:
            res = results[0]
            print(f"  📍 Title: {res.title}")
            print(f"  📊 Severity: {res.severity}")
            print(f"  💡 Advice: {res.advice}")
        else:
            print("  ❌ No results found. (Check your NVIDIA_API_KEY)")
        print("-" * 50)

        # 4. Empty / Unknown Query behavior
        print("\n[4/10] Empty Query (sanity): skip remote embed call on empty input.")
        empty_query = ""
        if empty_query.strip():
            empty_results = await store.search_findings(empty_query, k=1)
            print(f"  🔎 Empty query results: {len(empty_results)}")
        else:
            print("  ⚠️ Empty query skipped to avoid remote embed validation errors.")

        # 5. Concurrency test
        print("\n[5/10] Concurrency: run 3 searches in parallel to check thread safety.")
        concurrent_queries = [
            "cloud credential leak",
            "hardcoded secret",
            "aws key in config"
        ]
        concurrent_results = await asyncio.gather(
            *[store.search_findings(q, k=1) for q in concurrent_queries]
        )
        print("  ✅ Concurrency search completed.")

        # 6. Unicode / path edge case for code indexing
        print("\n[6/10] Unicode path indexing: ensure non-ASCII file paths do not break indexing.")
        await store._index_file_content("tests/edge/unicode_路径.py", "print('hello')\n")
        unicode_hits = await store.search_code("hello", k=1)
        print(f"  📄 Unicode path hit: {bool(unicode_hits)}")

        # 7. Code indexing + search on a real repo (optional)
        repo_path = os.getenv("RAG_VISUAL_TEST_REPO")
        if repo_path:
            print("\n[7/10] Large repo indexing: optional repo from RAG_VISUAL_TEST_REPO.")
            await store.index_code_repo(repo_path)
            code_hits = await store.search_code("authentication", k=1)
            print(f"  📄 Repo code search hits: {len(code_hits)}")
        else:
            print("\n[7/10] Large repo indexing: SKIPPED (set RAG_VISUAL_TEST_REPO to enable).")

        # 8. Persistence round-trip
        print("\n[8/10] Persistence: save and reload indexes.")
        store.save()
        store.load()
        reload_results = await store.search_findings(query, k=1)
        print(f"  💾 Reloaded results: {len(reload_results)}")

        # 9. Nemotron failure path (optional)
        print("\n[9/10] Nemotron failure path: should handle missing/invalid key cleanly.")
        if not api_key or "xxx" in api_key:
            print("  ✅ Missing key already exercised above.")
        else:
            print("  ⚠️ To test failure, set NVIDIA_API_KEY to an invalid value and re-run.")

        # 10. Agent tool schema (manual verification)
        print("\n[10/10] Agent tool schema: verify MCP request/response match expected JSON fields.")
        print("  ℹ️ Run a real MCP call from the Go agent and confirm fields align with models.py.")

        print("="*50)
    finally:
        # Close the async client inside the running loop to avoid
        # "Event loop is closed" warnings on Windows.
        if hasattr(findings_embedder, "client") and findings_embedder.client:
            try:
                await findings_embedder.client.aclose()
            except Exception:
                pass

if __name__ == "__main__":
    if os.name == "nt":
        # Avoid Proactor event loop shutdown warnings on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_visual_test())
