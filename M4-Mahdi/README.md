# M4-Mahdi — Energy planning & optimization backend (Tunisia)

Backend of module **M4 — Agentic Energy Planning and Optimization System for Tunisia**.
It integrates M1 demand forecasts and M3 international benchmarks, simulates the electricity system month by
month, optimizes long-term capacity expansion (LP) and short-term operation of the existing fleet (MILP with integer
units in service) with **Pyomo + HiGHS**, generates and explains alternative strategies through a **LangGraph** agent
with human approval and a recommendation under the user's stated priorities, and exposes everything through a
**FastAPI** REST API.

> **Data status: MOCK.** All inputs in `data/input/` are synthetic or fictional (see `docs/DATA_INTEGRATION_GUIDE.md`).
> Results demonstrate the method; they are not Tunisian statistics or investment recommendations.

## Quick start (Windows PowerShell)

```powershell
cd M4-Mahdi
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python scripts\init_db.py
.\.venv\Scripts\python scripts\seed_demo.py
.\.venv\Scripts\python -m uvicorn main:app --reload --port 8000
```

Linux/macOS: replace `.\.venv\Scripts\python` by `.venv/bin/python` and `py -3.12` by `python3.12`.

- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/api/health
- Tests: `.\.venv\Scripts\python -m pytest` (live LLM test opt-in: `$env:M4_LIVE_LLM=1`)
- LLM check: `.\.venv\Scripts\python scripts\check_llm.py` (key, model availability, one JSON and one tool call)

Demo accounts (development only, created by `seed_demo.py` from the values in `.env`):
`admin` / `DemoAdmin-2026` (ADMIN) and `planner` / `DemoPlanner-2026` (USER). Change them in `.env`.
`seed_demo.py` refuses to run when `APP_ENV=production`; use `scripts/create_admin.py` instead.

## Python version

Tested with **Python 3.12**. If your machine has Python 3.15 (or 3.14), install 3.12 side by side
(`winget install Python.Python.3.12`, the python.org installer, or `py install 3.12` with the Python install
manager) and create the virtual environment with `py -3.12`. Your existing Python is not modified.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | development | `production` makes `JWT_SECRET_KEY` mandatory and disables demo seeding |
| `JWT_SECRET_KEY` | (empty → ephemeral dev secret) | token signing key |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | 30 / 7 | token lifetimes |
| `CORS_ORIGINS` | Vite dev server | allowed browser origins |
| `DATABASE_URL` | `sqlite:///./data/m4.db` | SQLite file or PostgreSQL URL |
| `DATA_SOURCES_FILE` | `data/input/sources.yml` | input manifest (MOCK/REAL declaration) |
| `SOLVER_NAME` | `appsi_highs` | Pyomo HiGHS interface (`highs` also works) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenRouter, empty, `nvidia/nemotron-3.5-super-vl:free` | OpenAI-compatible LLM; empty key → deterministic agent |
| `LLM_FALLBACK_MODELS` | two free models | tried in order when the primary model is rate-limited (OpenRouter routing) |
| `LLM_REASONING_EFFORT` / `LLM_MAX_TOKENS` | `none` / 4000 | disable hidden reasoning of reasoning models (retried with `low` if mandatory) |
| `LLM_DAILY_CALL_LIMIT` | 45 | calls per UTC day before the agent switches to deterministic mode (free keys: 50/day) |
| `DEMO_*` | dev values | demo accounts for `seed_demo.py` |

## Layout

```
M4-Mahdi/
├── main.py                    # uvicorn entry point (main:app)
├── m4/
│   ├── config.py              # settings from .env, relative paths only
│   ├── data_integration/      # load, validate, prepare, adapters, provenance, quality report
│   ├── scenarios/             # ScenarioConfig schema + 5 predefined scenarios
│   ├── modeling/              # demand pathway, system inputs, simulation, indicators
│   ├── optimization/          # long-term Pyomo LP, HiGHS wrapper, multi-objective engine, validation, diagnostics
│   ├── operations/            # short-term operational MILP: units in service, dispatch, strategies, validation
│   ├── strategy/              # alternative strategies, roadmaps, trade-offs
│   ├── agent/                 # LangGraph workflow, tools, LLM client + usage ledger, intent, report, explanations
│   ├── auth/  db/  api/       # security, persistence, REST API
│   └── services.py            # orchestration between API, engine and database
├── data/input/                # sources.yml + MOCK inputs (m1, m3, reference_2024, assumptions)
├── data/mock/legacy/          # original prototype mock files
├── scripts/                   # init_db, seed_demo, create_admin, generate_synthetic_data, export_openapi, check_llm
├── tests/                     # pytest suite
└── docs/                      # ARCHITECTURE, MATHEMATICAL_MODEL, API, DATA_INTEGRATION_GUIDE, openapi.json
```

## Documentation

- `docs/ARCHITECTURE.md` — components, data flow, agent workflow, security, persistence
- `docs/MATHEMATICAL_MODEL.md` — sets, variables, constraints, objectives, units, multi-objective methods, limitations
- `docs/API.md` + `docs/openapi.json` — REST API
- `docs/DATA_INTEGRATION_GUIDE.md` — replacing mock data by real M1/M3 outputs
- `TEAM_INTEGRATION_GUIDE.md` — integrating this folder into the team repository

## Main limitations

Monthly resolution (no hourly dispatch, peak via ratio, no storage), no construction lead times, mock data,
no Alembic (new columns are added at startup), no login rate limiting, PostgreSQL not tested; free LLM models are
rate-limited (deterministic fallback).
Details in `docs/MATHEMATICAL_MODEL.md` and `docs/ARCHITECTURE.md`.
