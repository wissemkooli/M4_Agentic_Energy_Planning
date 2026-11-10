# M4 — Agentic Energy Planning and Optimization System for Tunisia

**Live demo: [http://129.151.225.146](http://129.151.225.146)** — MOCK data; sign in with the demo accounts listed below.

Decision-support application for electricity planning and operation in Tunisia (reference year 2024). A user
describes a problem in natural language — a **long-term planning goal** (“reduce CO2 by 2035 with a limited budget”)
or a **short-term operating problem** (“demand increases next month, production capacity is limited, minimize
operating cost while satisfying demand”). An agent understands it, retrieves the relevant M1/M3/reference data,
asks for missing values, formulates and checks a mathematical optimization problem (LP for planning, MILP for
operation); after the user approves it, Pyomo/HiGHS computes several alternative strategies, the agent explains them
in plain language and recommends one **under the stated priorities**; the user marks a preferred option and an
administrator records the final decision.

> **The agent proposes, the optimizer calculates, the human decides.**
> The LLM never produces numerical results; an approved strategy is a recommendation, not an authorization to invest
> or to operate a real system. Nothing is executed automatically.

> **Data status: MOCK.** M1 forecasts, M3 benchmarks, the 2024 reference system and technology assumptions in this
> repository are synthetic or fictional and labelled as such everywhere (API and UI). See
> `M4-Mahdi/docs/DATA_INTEGRATION_GUIDE.md` to connect the real M1/M3 outputs.

## Repository structure

```
M4_Agentic_Energy_Planning/
├── M4-Mahdi/              # BACKEND (independent, reusable): FastAPI, data integration, simulation,
│   ├── m4/                #   Pyomo/HiGHS optimization (LP planning, MILP operation), LangGraph agent, auth, persistence
│   ├── data/              #   input manifest + MOCK datasets
│   ├── docs/              #   ARCHITECTURE, MATHEMATICAL_MODEL, API, DATA_INTEGRATION_GUIDE, openapi.json
│   ├── scripts/  tests/
│   ├── README.md  TEAM_INTEGRATION_GUIDE.md  requirements*.txt  .env.example
├── frontend/              # proposed React + TypeScript UI (replaceable; talks to the backend over REST only)
├── scripts/               # setup.ps1, start-dev.ps1, start-backend.ps1, start-frontend.ps1, run-tests.ps1, setup.sh
├── architecture.yml       # architecture description consistent with the code
├── TEST_REPORT.md         # tests executed before delivery and their results
├── IMPLEMENTATION_REPORT.md  # what was found, implemented, reused, tested; final audit checklist
└── README.md
```

## Run M4 on another computer

Requirements: Windows 10/11 with PowerShell, **Python 3.12** (3.11–3.13 work; 3.14/3.15 not supported yet by all
binary dependencies), **Node.js 18.18+** (LTS 20/22 recommended), Git. No database server is required. An OpenRouter
API key is optional (without it the agent runs in deterministic mode with the same numerical engine).

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

### Enable the LLM (optional, OpenRouter)

```powershell
# 1. create a key at https://openrouter.ai/keys (a free key allows 50 requests/day on ':free' models)
# 2. put it ONLY in the backend .env (never in the frontend, never in Git):
notepad M4-Mahdi\.env          # set LLM_API_KEY=sk-or-...   (LLM_MODEL already points to a free model)
# 3. check the key, the model availability, one JSON call and one tool call (uses 2 requests):
M4-Mahdi\.venv\Scripts\python M4-Mahdi\scripts\check_llm.py
# 4. restart the backend (start-dev.ps1 or the uvicorn command below)
```

Defaults in `.env.example`: `LLM_MODEL=nvidia/nemotron-3.5-super-vl:free`, fallbacks
`nvidia/nemotron-3-ultra-550b-a55b:free,openrouter/free` (free models are often rate-limited; OpenRouter then routes
to the next one), `LLM_REASONING_EFFORT=none` (fast answers; hidden reasoning would consume the token budget),
`LLM_DAILY_CALL_LIMIT=45` (stays under the 50 free requests/day; afterwards the agent continues deterministically).
Free models change over time: `check_llm.py` reports whether the configured models are still listed.
Each workflow round uses at most 3 LLM calls (intent, tool selection, final narrative); the request page shows the
calls, tokens and the model that answered.

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
# optional, uses real LLM quota (about 4-6 requests):
cd M4-Mahdi; $env:M4_LIVE_LLM=1; .\.venv\Scripts\python -m pytest tests\test_live_llm.py -s; Remove-Item Env:M4_LIVE_LLM; cd ..
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
| Agent says “LLM interpretation failed (… HTTP 429 …)” | the free model is rate-limited; the deterministic parser was used. Wait, add fallbacks in `LLM_FALLBACK_MODELS`, or use a paid model |
| “HTTP 402 / insufficient credits” | the configured model is not free: use a `:free` model or add credits |
| “Daily LLM call budget reached” | `LLM_DAILY_CALL_LIMIT` calls were made today (UTC); raise it if your key allows more |

## Environment variables

Backend (`M4-Mahdi/.env`, template `.env.example`): `APP_ENV`, `JWT_SECRET_KEY`, `ACCESS_TOKEN_MINUTES`,
`REFRESH_TOKEN_DAYS`, `REFRESH_COOKIE_SECURE`, `CORS_ORIGINS`, `DATABASE_URL`, `DATA_SOURCES_FILE`, `SOLVER_NAME`,
`SOLVER_TIME_LIMIT_SECONDS`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODELS`,
`LLM_REASONING_EFFORT`, `LLM_MAX_TOKENS`, `LLM_DAILY_CALL_LIMIT`, `LLM_TIMEOUT_SECONDS`, `LLM_TEMPERATURE`,
`DEMO_ADMIN_*`, `DEMO_USER_*`, optional `M4_ADMIN_*` for `create_admin.py`.

Frontend (`frontend/.env`): `VITE_API_BASE_URL` (empty = same origin through the Vite proxy), `VITE_BACKEND_URL`.

The LLM is optional. Without `LLM_API_KEY` the agent runs in deterministic mode (rule-based interpretation,
default investigation plan, template explanations) with the same workflow and the same numerical engine.
With a key (OpenRouter or any OpenAI-compatible API) the LLM interprets requests, selects investigation tools and
rephrases the final decision report under anti-fabrication checks (a text containing a number that is not in the
verified results is rejected and the template text is used).

## What the application does

1. **Energy dashboard** — 2024 reference system, demand outlook with 80 % interval, capacity, generation mix,
   renewable share, gas dependence, emissions, M3 benchmark context, data-quality warnings.
2. **Planning agent** — conversation for long-term planning and short-term operational problems; problem
   understanding (objectives, decision variables, resources, constraints, horizon, data, missing information);
   clarification questions with computed context; investigation report (tools executed); proposed formulation with
   validation checks and a feasibility pre-check, for approval; workflow status of every LangGraph node; trade-off
   table from validated solver results; decision report with a recommendation under the stated priorities, a
   plain-language explanation and “prefer this option” buttons; LLM calls and tokens of the request.
3. **Scenario builder** — 5 predefined scenarios (Reference, Energy Efficiency, Accelerated Renewables, Balanced
   Transition, Stress) and custom scenarios: horizon, demand basis (M1 forecast or interval bound), efficiency,
   budgets, renewable/CO2/gas targets, reserve margin, imports, technology limits, objective method
   (single, weighted sum, ε-constraint, lexicographic); validate, save, simulate, optimize, Pareto analysis.
4. **Operational problems (MILP)** — gas units kept in service (integer), monthly dispatch, imports and unserved
   energy for 1–24 months with outages, demand changes, fuel limits and reserve; strategies least-cost, reliability
   first, minimum gas, balanced; monthly schedule of what to do; independent validation (integrality, minimum output).
5. **Optimization results** — solver status, objective values, investments, capacity evolution, generation,
   renewable share, emissions, gas and import dependence, investment requirements, constraint satisfaction,
   independent validation, solver log, provenance; infeasibility diagnosis when no solution exists.
6. **Strategies & roadmaps** — alternatives with annual roadmap, milestones, advantages, disadvantages, risks,
   trade-off vs least-cost (abatement cost), assumptions, decisions and versions.
7. **Comparison** and **History**.
8. **Administrator portal** — overview (including LLM usage), review queue showing the agent's recommendation and the
   requester's choice, accept / reject (mandatory reason) / request revision, decision audit trail, user management.
9. **Data sources & assumptions** — provenance (status, version, SHA-256, transformations), quality report,
   2024 reconciliation, technology assumptions.

## Decisions taken where the specification was ambiguous or contradictory

| Topic | Decision |
|---|---|
| Team repository not available | Built in the independent-development mode of the specification: synthetic M1/M3 data in the expected schemas, adapters + manifest for the real outputs. The original M4 functions (load/validate/prepare/prepare_demand) were re-implemented from their description in `m4/data_integration/`; compare them with your existing versions when merging. No repository containing M1, M2 or M3 was found on the GitHub accounts available to the build (see `IMPLEMENTATION_REPORT.md`). |
| Example operational request (“next month”, “limited capacity”) vs a long-term planner | A second problem class was added (`m4/operations`, MILP) instead of forcing it into the investment LP. “Next month” is anchored on the M1 data (first forecast month), not on the server date. |
| Recommendation vs “human decides” | The agent recommends under the priorities the user stated (explicit rule, shown with the alternatives that win on other criteria); the user marks a preferred option; only an administrator records a decision; nothing is executed. |
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
- Free LLM models are rate-limited (50 requests/day per free key) and change over time; the agent falls back to
  deterministic logic when they fail. Live LLM runs were executed with OpenRouter free models (see `TEST_REPORT.md`);
  the automated suite uses a fake client so it never depends on quota.
- Operational model: monthly resolution (no hourly unit commitment, ramping or start-up constraints); unit size,
  minimum output, unit fixed cost and value of lost load are MOCK parameters.
- Not tested: PostgreSQL, the PowerShell scripts on a real Windows machine (the equivalent `setup.sh` flow was tested
  on Linux). See `TEST_REPORT.md`.
- No Alembic migrations; no login rate limiting.

## Further documentation

- `M4-Mahdi/README.md` — backend usage · `M4-Mahdi/TEAM_INTEGRATION_GUIDE.md` — merging into the team repository
- `M4-Mahdi/docs/ARCHITECTURE.md` · `M4-Mahdi/docs/MATHEMATICAL_MODEL.md` · `M4-Mahdi/docs/API.md`
- `M4-Mahdi/docs/DATA_INTEGRATION_GUIDE.md` — exactly which files to replace with the real M1/M3 outputs
- `architecture.yml` · `TEST_REPORT.md` · `frontend/README.md`
