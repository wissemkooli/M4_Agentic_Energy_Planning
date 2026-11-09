<#
.SYNOPSIS
  One-time setup of M4 on Windows: backend virtual environment, dependencies, .env files,
  database initialisation, demo accounts and frontend dependencies.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Python "C:\Python312\python.exe"
#>
param(
  [string]$Python = "",          # explicit interpreter path (optional)
  [switch]$SkipFrontend,
  [switch]$NoDemoAccounts
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "M4-Mahdi"
$Frontend = Join-Path $Root "frontend"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. Python
Step "Locating a compatible Python (3.11-3.13, 3.12 recommended)"
$PyExe = $null; $PyArgs = @()
if ($Python) { $PyExe = $Python }
elseif (Get-Command py -ErrorAction SilentlyContinue) {
  foreach ($v in @("3.12", "3.13", "3.11")) {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $PyExe = "py"; $PyArgs = @("-$v"); break }
  }
}
if (-not $PyExe -and (Get-Command python -ErrorAction SilentlyContinue)) { $PyExe = "python" }
if (-not $PyExe) { Fail "Python not found. Install Python 3.12: winget install Python.Python.3.12" }
$ver = & $PyExe @PyArgs -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
Write-Host "Using Python $ver ($PyExe $PyArgs)"
$minor = [int]($ver.Split(".")[1])
if ($minor -lt 11 -or $minor -gt 13) {
  Fail ("Python $ver is not supported by all dependencies (highspy, pydantic-core wheels). " +
        "Install Python 3.12 side by side (winget install Python.Python.3.12) and re-run, " +
        "or pass -Python <path-to-python3.12>. Your current Python is left untouched.")
}

# ---------------------------------------------------------------- 2. Backend
Step "Creating backend virtual environment (M4-Mahdi\.venv)"
Push-Location $Backend
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $PyExe @PyArgs -m venv .venv }
$VPy = Join-Path $Backend ".venv\Scripts\python.exe"
& $VPy -m pip install --upgrade pip --quiet
Step "Installing backend dependencies"
& $VPy -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see messages above)." }

Step "Configuring M4-Mahdi\.env"
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  $secret = & $VPy -c "import secrets; print(secrets.token_urlsafe(48))"
  (Get-Content ".env") -replace '^JWT_SECRET_KEY=$', "JWT_SECRET_KEY=$secret" | Set-Content ".env" -Encoding UTF8
  Write-Host "Created .env with a new random JWT secret."
} else { Write-Host ".env already exists: kept as is." }

Step "Initialising the database and loading predefined scenarios"
& $VPy scripts\init_db.py
if ($LASTEXITCODE -ne 0) { Fail "Database initialisation failed." }
if (-not $NoDemoAccounts) {
  Step "Creating development demo accounts"
  & $VPy scripts\seed_demo.py
}
Pop-Location

# ---------------------------------------------------------------- 3. Frontend
if (-not $SkipFrontend) {
  Step "Checking Node.js (18.18 or newer)"
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "Node.js not found. Install the LTS version: winget install OpenJS.NodeJS.LTS" }
  $nodeVer = (node --version).TrimStart("v")
  $major = [int]($nodeVer.Split(".")[0])
  if ($major -lt 18) { Fail "Node.js $nodeVer is too old; install Node 18.18+ (LTS 20 or 22 recommended)." }
  Write-Host "Node.js $nodeVer"
  Push-Location $Frontend
  if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
  Step "Installing frontend dependencies (npm install)"
  npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { Fail "npm install failed." }
  Pop-Location
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Start everything:   powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1"
Write-Host "Backend only:       powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1"
Write-Host "Demo accounts:      admin / DemoAdmin-2026 (ADMIN), planner / DemoPlanner-2026 (USER) - see M4-Mahdi\.env"
