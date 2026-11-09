#!/usr/bin/env bash
# One-time setup on Linux/macOS (equivalent of setup.ps1).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$(command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3)}"
VER=$("$PY" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
case "$VER" in 3.11|3.12|3.13) ;; *) echo "Python $VER unsupported; install 3.12 (set PYTHON=/path/to/python3.12)"; exit 1;; esac
cd "$ROOT/M4-Mahdi"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements-dev.txt
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$(.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(48))')
  sed -i.bak "s|^JWT_SECRET_KEY=$|JWT_SECRET_KEY=$SECRET|" .env && rm -f .env.bak
fi
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/seed_demo.py
cd "$ROOT/frontend"
[ -f .env ] || cp .env.example .env
npm install --no-audit --no-fund
echo "Setup complete. Backend: cd M4-Mahdi && .venv/bin/python -m uvicorn main:app --reload --port 8000 ; Frontend: cd frontend && npm run dev"
