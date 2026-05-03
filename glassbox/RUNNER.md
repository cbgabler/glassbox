# GlassBox One-Command Runner

Run backend, frontend, and RAG server from one command.

## From Git Bash
```bash
cd glassbox
bash ./run-all.sh
```

## From PowerShell
```powershell
cd glassbox
.\run-all.ps1
```

## What these scripts do
- Ensure backend binary exists (build if needed)
- Ensure frontend dependencies are installed
- Ensure ragserver venv exists (create + install requirements if needed)
- Start all three services

## Stop services
- `run-all.sh`: press `Ctrl+C` in the same terminal
- `run-all.ps1`: close spawned windows or run:
```powershell
Stop-Process -Id <PID>
```

