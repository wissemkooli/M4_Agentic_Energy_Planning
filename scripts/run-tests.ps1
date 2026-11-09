# Run backend (pytest) and frontend (Vitest) tests.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root "M4-Mahdi")
& .\.venv\Scripts\python.exe -m pytest -q
$backend = $LASTEXITCODE
Pop-Location
Push-Location (Join-Path $Root "frontend")
npm test
$frontend = $LASTEXITCODE
Pop-Location
if ($backend -ne 0 -or $frontend -ne 0) { Write-Host "Some tests failed." -ForegroundColor Red; exit 1 }
Write-Host "All tests passed." -ForegroundColor Green
