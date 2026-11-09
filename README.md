# M4 — Agentic Energy Planning and Optimization System for Tunisia

Decision-support application for long-term electricity planning in Tunisia (reference year 2024, default horizon
2025–2040). A user describes objectives and constraints; an agent investigates the data, asks for missing values and
proposes a mathematical formulation; after approval, Python simulates the system and Pyomo/HiGHS computes several
alternative strategies; an administrator compares them and accepts or rejects them.

> **The agent proposes, the optimizer calculates, the administrator decides.**
> The LLM never produces numerical results; an approved strategy is a planning recommendation, not an authorization to invest.

> **Data status: MOCK.** M1 forecasts, M3 benchmarks, the 2024 reference system and technology assumptions in this
> repository are synthetic or fictional and labelled as such everywhere (API and UI). See
> `M4-Mahdi/docs/DATA_INTEGRATION_GUIDE.md` to connect the real M1/M3 outputs.

## Repository structure

```
M4_Agentic_Energy_Planning/
├── M4-Mahdi/              # BACKEND (independent, reusable): FastAPI, data integration, simulation,
│   ├── m4/                #   Pyomo/HiGHS optimization, LangGraph agent, auth, persistence
│   ├── data/              #   input manifest + MOCK datasets
│   ├── docs/              #   ARCHITECTURE, MATHEMATICAL_MODEL, API, DATA_INTEGRATION_GUIDE, openapi.json
│   ├── scripts/  tests/
│   ├── README.md  TEAM_INTEGRATION_GUIDE.md  requirements*.txt  .env.example
├── frontend/              # proposed React + TypeScript UI (replaceable; talks to the backend over REST only)
├── scripts/               # setup.ps1, start-dev.ps1, start-backend.ps1, start-frontend.ps1, run-tests.ps1, setup.sh
├── architecture.yml       # architecture description consistent with the code
├── TEST_REPORT.md         # tests executed before delivery and their results
└── README.md
```

## Run M4 on another computer

Requirements: Windows 10/11 with PowerShell, **Python 3.12** (3.11–3.13 work; 3.14/3.15 not supported yet by all
binary dependencies), **Node.js 18.18+** (LTS 20/22 recommended), Git. No database server and no API key are required.

```powershell
# 0. (only if needed) install the prerequisites side by side with what you already have
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS

# 1. get the code
git clone <team-repo-url>
cd <team-repo>\M4_Agentic_Energy_Planning        # or wherever the M4 folder is

# 2. one-time setup: venv, dependencies, .env files (random JWT secret), database, demo accounts, npm install
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# 3. start backend + frontend (two windows) and open the browser
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
```

Then open http://localhost:5173 and sign in with a development demo account:

| Role | Username | Password |
|---|---|---|
| ADMIN | `admin` (or `admin@m4.local`) | `DemoAdmin-2026` |
| USER | `planner` (or `planner@m4.local`) | `DemoPlanner-2026` |

These accounts are created from `M4-Mahdi/.env` by `scripts/seed_demo.py` for local testing only (refused when
`APP_ENV=production`). For a real deployment create the first administrator with
`M4-Mahdi\.venv\Scripts\python M4-Mahdi\scripts\create_admin.py` (asks for the credentials; nothing is hard-coded).

### Manual start (if you prefer separate terminals)

```powershell
# terminal 1 — backend (http://127.0.0.1:8000, docs at /docs)
cd M4-Mahdi
.\.venv\Scripts\python -m uvicorn main:app --reload --port 8000

# terminal 2 — frontend (http://localhost:5173)
cd frontend
npm run dev
```

Manual installation without the setup script: see `M4-Mahdi/README.md` and `frontend/README.md`.
Linux/macOS: `bash scripts/setup.sh`, then start both servers as above with `.venv/bin/python`.

### Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-tests.ps1
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `setup.ps1` says the Python version is unsupported | install Python 3.12 and re-run, or `-Python C:\path\to\python.exe` |
| `running scripts is disabled on this system` | use `powershell -ExecutionPolicy Bypass -File ...` as shown |
| Frontend shows “Cannot reach the M4 backend” | start the backend; check `VITE_BACKEND_URL` in `frontend/.env` (use `127.0.0.1`, not `localhost`) |
| Logged out after every backend restart | set `JWT_SECRET_KEY` in `M4-Mahdi/.env` (setup.ps1 does it) |
| Port 8000 or 5173 busy | stop the other process or change the port (`--port`, `vite --port`) and `VITE_BACKEND_URL` |
| Reset everything | delete `M4-Mahdi/data/m4.db`, run `scripts\init_db.py` and `scripts\seed_demo.py` |

## Environment variables

Backend (`M4-Mahdi/.env`, template `.env.example`): `APP_ENV`, `JWT_SECRET_KEY`, `ACCESS_TOKEN_MINUTES`,
`REFRESH_TOKEN_DAYS`, `REFRESH_COOKIE_SECURE`, `CORS_ORIGINS`, `DATABASE_URL`, `DATA_SOURCES_FILE`, `SOLVER_NAME`,
`SOLVER_TIME_LIMIT_SECONDS`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`,
`LLM_TEMPERATURE`, `DEMO_ADMIN_*`, `DEMO_USER_*`, optional `M4_ADMIN_*` for `create_admin.py`.

Frontend (`frontend/.env`): `VITE_API_BASE_URL` (empty = same origin through the Vite proxy), `VITE_BACKEND_URL`.

The LLM is optional. Without `LLM_API_KEY` the agent runs in deterministic mode (rule-based interpretation,
default investigation plan, template explanations) with the same workflow and the same numerical engine.
With a key (OpenRouter or any OpenAI-compatible API) the LLM interprets requests, selects investigation tools and
rephrases explanations under anti-fabrication checks.

## What the application does

1. **Energy dashboard** — 2024 reference system, demand outlook with 80 % interval, capacity, generation mix,
   renewable share, gas dependence, emissions, M3 benchmark context, data-quality warnings.
2. **Planning agent** — conversation; clarification questions with computed context; investigation report
   (tools executed); proposed formulation (objective, constraints, alternative strategies, assumptions) for approval;
   workflow status of every LangGraph node; trade-off table from validated solver results.
3. **Scenario builder** — 5 predefined scenarios (Reference, Energy Efficiency, Accelerated Renewables, Balanced
   Transition, Stress) and custom scenarios: horizon, demand basis (M1 forecast or interval bound), efficiency,
   budgets, renewable/CO2/gas targets, reserve margin, imports, technology limits, objective method
   (single, weighted sum, ε-constraint, lexicographic); validate, save, simulate, optimize, Pareto analysis.
4. **Optimization results** — solver status, objective values, investments, capacity evolution, generation,
   renewable share, emissions, gas and import dependence, investment requirements, constraint satisfaction,
   independent validation, solver log, provenance; infeasibility diagnosis when no solution exists.
5. **Strategies & roadmaps** — alternatives with annual roadmap, milestones, advantages, disadvantages, risks,
   trade-off vs least-cost (abatement cost), assumptions, decisions and versions.
6. **Comparison** and **History**.
7. **Administrator portal** — overview, review queue, accept / reject (mandatory reason) / request revision,
   decision audit trail, user and role management.
8. **Data sources & assumptions** — provenance (status, version, SHA-256, transformations), quality report,
   2024 reconciliation, technology assumptions.

## Decisions taken where the specification was ambiguous or contradictory

| Topic | Decision |
|---|---|
| Team repository not available | Built in the independent-development mode of the specification: synthetic M1/M3 data in the expected schemas, adapters + manifest for the real outputs. The original M4 functions (load/validate/prepare/prepare_demand) were re-implemented from their description in `m4/data_integration/`; compare them with your existing versions when merging. |
| “Phase 1 only, wait for approval” vs “deliver two tested ZIPs” | The final requirement (complete, tested deliverables) was followed; the architecture is documented in `M4-Mahdi/docs/ARCHITECTURE.md` and `architecture.yml`. |
| Dark navy design (section 32) vs black/white/gold (section 23) | Black/white/gold, the later and more specific requirement. |
| `backend/` folder (section 21) vs `M4-Mahdi/` (section 26) | `M4-Mahdi/` is the backend folder; Python package `m4`. |
| Legacy mock `m3_benchmark.csv` with costs/CFs | Not M3 output: kept in `data/mock/legacy/`; technical parameters live in `data/input/assumptions/` (documented MOCK values). |
| PostgreSQL vs “no local services required” | SQLite by default (zero setup), PostgreSQL supported through `DATABASE_URL` (not tested). |
| Batteries | Not modelled: a monthly balance cannot represent daily storage cycles credibly. Documented extension path. |
| “LLM decides tools” vs “never fabricate” | LLM chooses read-only investigation tools; mandatory checks always run; optimization only after human approval; LLM numbers are checked against the user text / verified results. |
| 2024 demand ≠ 2024 generation in the mock data | Not forced to balance; reported in the data quality report. |

## Limitations

- All data are MOCK; predefined scenario targets are illustrative, not official targets.
- Monthly resolution: peak demand via a ratio, variable renewables limited by a monthly share (proxy for flexibility),
  no storage, no hourly dispatch, no grid constraints, no construction lead times.
- With the mock costs, solar and wind are cheaper than gas fuel, so even least-cost plans build renewables up to
  the deployment limits; results are sensitive to these mock assumptions.
- Emission accounting covers domestic production only (imports at 0 tCO2/MWh by default).
- Not tested: live LLM providers (only a fake client in tests), PostgreSQL, the PowerShell scripts on a real Windows
  machine (the equivalent `setup.sh` flow was tested on Linux). See `TEST_REPORT.md`.
- No Alembic migrations; no login rate limiting.

## Further documentation

- `M4-Mahdi/README.md` — backend usage · `M4-Mahdi/TEAM_INTEGRATION_GUIDE.md` — merging into the team repository
- `M4-Mahdi/docs/ARCHITECTURE.md` · `M4-Mahdi/docs/MATHEMATICAL_MODEL.md` · `M4-Mahdi/docs/API.md`
- `M4-Mahdi/docs/DATA_INTEGRATION_GUIDE.md` — exactly which files to replace with the real M1/M3 outputs
- `architecture.yml` · `TEST_REPORT.md` · `frontend/README.md`
