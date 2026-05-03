Param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$backendDir = Join-Path $scriptDir "backend"
$frontendDir = Join-Path $scriptDir "frontend"
$ragDir = Join-Path $backendDir "ragserver"

Write-Host "[1/3] Preparing backend..."
Push-Location $backendDir
try {
    if (-not (Test-Path ".\main.exe")) {
        Write-Host "main.exe not found. Building backend..."
        & powershell -ExecutionPolicy Bypass -File ".\build-all.ps1"
    }
} finally {
    Pop-Location
}

Write-Host "[2/3] Preparing frontend..."
Push-Location $frontendDir
try {
    if (-not (Test-Path ".\node_modules")) {
        & npm install
    }
} finally {
    Pop-Location
}

Write-Host "[3/3] Preparing RAG server..."
if (-not (Test-Path $ragDir)) {
    throw "RAG server directory not found at $ragDir"
}
Push-Location $ragDir
try {
    if (-not (Test-Path ".\venv\Scripts\python.exe")) {
        Write-Host "ragserver venv missing. Creating venv + installing requirements..."
        & python -m venv venv
        & .\venv\Scripts\python.exe -m pip install -r requirements.txt
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Starting services in separate windows..."

$backendProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backendDir'; .\main.exe" -PassThru
$frontendProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontendDir'; npm run dev" -PassThru
$ragProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ragDir'; .\venv\Scripts\python.exe -m uvicorn server:app --reload --port 8000" -PassThru

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host "Frontend PID: $($frontendProc.Id)"
Write-Host "RAG PID: $($ragProc.Id)"
Write-Host ""
Write-Host "Use Stop-Process -Id <PID> to stop any service."

