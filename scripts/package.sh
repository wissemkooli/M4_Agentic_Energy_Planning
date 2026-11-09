#!/usr/bin/env bash
# Build the two delivery ZIPs from a clean copy (no venv, node_modules, .env, database, caches).
#   M4_Agentic_Energy_Planning.zip  -> full application (backend + frontend)
#   M4-Mahdi-Backend.zip            -> backend only (with a copy of architecture.yml)
# Usage: bash scripts/package.sh <output-dir>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mkdir -p "${1:-$ROOT/..}" && cd "${1:-$ROOT/..}" && pwd)"
STAGE="$(mktemp -d)"
EXCLUDES=(--exclude .venv --exclude node_modules --exclude dist --exclude __pycache__ --exclude .pytest_cache
          --exclude '*.pyc' --exclude .env --exclude '*.db' --exclude '*.db-journal' --exclude '*.log'
          --exclude .git --exclude '*.tsbuildinfo')
mkdir -p "$STAGE/M4_Agentic_Energy_Planning"
rsync -a "${EXCLUDES[@]}" "$ROOT/" "$STAGE/M4_Agentic_Energy_Planning/"
rsync -a "${EXCLUDES[@]}" "$ROOT/M4-Mahdi/" "$STAGE/M4-Mahdi/"
cp "$ROOT/architecture.yml" "$STAGE/M4-Mahdi/architecture.yml"
rm -f "$OUT/M4_Agentic_Energy_Planning.zip" "$OUT/M4-Mahdi-Backend.zip"
(cd "$STAGE" && zip -qr "$OUT/M4_Agentic_Energy_Planning.zip" M4_Agentic_Energy_Planning)
(cd "$STAGE" && zip -qr "$OUT/M4-Mahdi-Backend.zip" M4-Mahdi)
rm -rf "$STAGE"
ls -l "$OUT"/M4_Agentic_Energy_Planning.zip "$OUT"/M4-Mahdi-Backend.zip
