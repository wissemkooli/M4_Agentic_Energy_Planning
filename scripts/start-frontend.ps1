# Start the React development server on http://localhost:5173 (proxies /api to the backend)
$ErrorActionPreference = "Stop"
$Frontend = Join-Path (Split-Path -Parent $PSScriptRoot) "frontend"
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) { Write-Host "Frontend not set up. Run scripts\setup.ps1 first." -ForegroundColor Red; exit 1 }
Set-Location $Frontend
npm run dev
