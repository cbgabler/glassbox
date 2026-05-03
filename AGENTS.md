# GlassBox agent guide

Use this as the single source of project context for AI coding agents.

## Key docs (read first)
- Project overview, architecture, run commands, env vars: [docs/platform-readme.md](docs/platform-readme.md)
- RAG server details and local dev notes: [glassbox/backend/ragserver/README.md](glassbox/backend/ragserver/README.md)

## Repo map (high level)
- glassbox/backend: Go entrypoints and server launcher
- glassbox/backend/ragserver: Python FastAPI MCP server + FAISS store
- glassbox/frontend: Vite + TypeScript frontend
- glassbox/backend/hardware: firmware and hardware test scripts

## Common commands
- Frontend dev server: `cd glassbox/frontend && npm install && npm run dev`
- Frontend build: `cd glassbox/frontend && npm run build`
- RAG server (PowerShell): see [glassbox/backend/ragserver/README.md](glassbox/backend/ragserver/README.md)

## Notes for agents
- Go module root: glassbox/backend (Go 1.25.6 in go.mod)
- RAG server uses Python 3.8+ and FAISS; keep changes local to ragserver unless requested
- agentconfig.yaml wires the Go agent to the MCP servers (including ragserver)
