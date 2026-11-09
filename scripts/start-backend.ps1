# Start the FastAPI backend on http://127.0.0.1:8000 (API docs at /docs)
$ErrorActionPreference = "Stop"
$Backend = Join-Path (Split-Path -Parent $PSScriptRoot) "M4-Mahdi"
$VPy = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) { Write-Host "Backend not set up. Run scripts\setup.ps1 first." -ForegroundColor Red; exit 1 }
Set-Location $Backend
& $VPy -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
