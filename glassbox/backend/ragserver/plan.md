# Vector Embeddings Visualizer Plan

## Goals
- Visualize embeddings (findings + code) in 2D/3D with realtime updates.
- Keep the visualizer usable for debugging and demos.
- Defer frontend integration until the standalone visualizer is proven.

## Recommended tool
- Arize Phoenix for a richer, standalone visualization UI (datasets, filters, search).
- Use UMAP for 2D/3D reduction; keep PCA as a fast, deterministic fallback.

## Phases

### Phase 1: Standalone visualizer (no frontend)
- Add a ragserver endpoint to export embeddings + metadata (findings + code).
- Build a small Python script that reads FAISS vectors + metadata and logs them to Phoenix.
- Color by type (finding vs code) and size by severity.

### Phase 2: Realtime updates (still standalone)
- Add a websocket endpoint that streams new embeddings as they are added.
- Stream new points to Phoenix for live updates.

## Open Questions
 - How many points are expected per run (10s, 1k, 100k)?
	 - Answer: expect a lot (code snippets + audit findings).
 - Should code snippets and findings live in the same plot or separate layers?
	 - Answer: separate layers.
 - Should we persist reduced coordinates or compute on the fly?

## Risks
- Large vector sets may require sampling or server-side reduction.
- Realtime UMAP is expensive; might need periodic batch updates.
- Phoenix adds a service dependency; plan for startup scripts and env config.
