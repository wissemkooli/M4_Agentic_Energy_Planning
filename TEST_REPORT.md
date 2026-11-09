# Test report

Executed before delivery on Linux (Ubuntu, Python 3.12.3, Node.js 18.19.1, Chromium via Playwright).
Package versions resolved from `requirements.txt` at test time: FastAPI 0.162, Pydantic 2.14, pandas 3.1,
NumPy 2.5, Pyomo 6.10.1, highspy 1.15.1, LangGraph 1.2, SQLAlchemy 2.1.

## 1. Backend automated tests — `M4-Mahdi/tests` (pytest)

Result: **94 passed, 0 failed** (≈ 70 s). Run twice: in the development folder and in a clean copy unzipped from
`M4-Mahdi-Backend.zip` with a fresh virtual environment and `pip install -r requirements-dev.txt`.

| File | Tests | Covers |
|---|---|---|
| `test_data_loading.py` | 13 | original 3 prototype checks (negative M1 forecast, capacity factor > 1, available > installed), duplicates, month format, interval order, missing months, missing columns, physical generation bound, missing file, reference totals, technology normalization, GWh→MW conversion (incl. leap year) |
| `test_adapters.py` | 13 | MOCK bundle, no XGBoost claim on synthetic data, legacy M1 schema, REAL-declared file with mock markers, REAL detection, multi-fold row selection, negative demand, missing months, M3 rejection of technical-parameter file, M3 rank consistency, missing technology assumption, manifest missing entry, 2024 reconciliation reported |
| `test_modeling.py` | 12 | M1 demand use, labelled extrapolation, interval basis without intervals, efficiency and upper bound, planning start before M1, unit conversions (potential, emissions baseline, CRF), retirements, unknown technology, simulation energy balance and limits, existing-fleet shortfall, capacity plan, negative plan rejected |
| `test_optimization.py` | 15 | optimal + validated small case, reserve margin, binding renewable target, binding budget, emission cap, impossible renewable target (infeasible + diagnosis, no results), insufficient budget, conflicting hard limits, min-emissions vs min-cost, weighted sum within utopia/nadir, ε-constraint bound, lexicographic tolerance, monotone Pareto frontier, solver unavailable, solver exception |
| `test_scenarios_strategies.py` | 9 | invalid horizon, target outside horizon, percentage/budget validation, objective-method validation, 5 predefined scenarios differ, reference and stress solve and validate, strategy generation (distinct, reported, validated), infeasible strategy request |
| `test_agent.py` | 16 | rule parser (example request, numeric constraints, billion conversion, unsupported technologies, inferred target year), intent merging, LLM ungrounded numbers discarded, LLM failure fallback, LLM not configured, LLM HTTP error and timeout, JSON parsing, tools execute real logic, grounding check, full workflow clarification → approval → strategies, infeasible request reported, LLM tool selection with mandatory checks and refusal of non-investigation tools, ungrounded LLM explanation rejected |
| `test_api.py` | 16 | public health, password hashing, valid login + cookie, invalid password, unauthenticated access 401, USER on admin endpoints 403, admin review endpoints, expired token, forged token, refresh rotation/grace/logout revocation, admin user management, dashboard/data endpoints, scenario CRUD + isolation, optimization/simulation/comparison/infeasible/Pareto endpoints, end-to-end agent + admin workflow, revision request → new version with preserved audit |

The LLM is tested with a deterministic fake client (no network). No live LLM provider was called.

## 2. Frontend automated tests — `frontend/src/test` (Vitest)

Result: **15 passed, 0 failed** (development folder and clean copy).
Covers: scenario form validation (horizon, targets, budgets, multi-objective settings), login validation, mandatory
rejection reason, new-user validation, redirect of unauthenticated users, validation before API call, backend error
display for invalid credentials, USER blocked from admin routes, admin review queue empty state from the API, backend
unreachable error state, empty runs state.

Type check (`tsc`, strict) and production build (`npm run build`): **passed** (development folder and clean copy).

## 3. Backend-only verification (no frontend)

From `M4-Mahdi-Backend.zip` unzipped into an empty folder: fresh venv, `pip install -r requirements-dev.txt`,
`.env` from `.env.example`, `scripts/init_db.py`, `scripts/seed_demo.py`, `uvicorn main:app --port 8100`, then an
HTTP script — **12/12 checks passed**:
health (solver `appsi_highs`, data MOCK) · unauthenticated 401 · invalid password 401 · USER on admin 403 ·
dashboard KPIs · reference optimization optimal + valid (1.4 s) · existing-fleet simulation · agent clarification
(budget, reserve margin) · formulation awaiting approval · approval → 3 strategies pending review (4.7 s) ·
admin reject + approve → planning reference · audit trail with 2 entries.

## 4. Full application, clean install, browser end-to-end

From `M4_Agentic_Energy_Planning.zip` unzipped into an empty folder: `bash scripts/setup.sh` (venv, pip install,
`.env` with generated JWT secret, database, demo accounts, `npm install`), backend on port 8000, Vite on 5173, then a
Playwright (Chromium) scenario — **all steps passed, 0 browser console errors**:

1. login page; planner login → energy dashboard
2. agent request “Reduce CO2 emissions by 2035 with a limited investment budget while keeping electricity supply
   reliable.” → clarification questions (budget with computed least-cost reference, reserve margin proposal)
3. answers (7,000 MUSD, accept 15 %) → proposed formulation → approval → trade-off table of 3 validated strategies
4. strategies page → side-by-side comparison
5. scenario builder: Balanced Transition optimization → results page; validation of a scenario formulation
6. data sources & assumptions page
7. USER opening `/admin` → access denied; logout with confirmation
8. admin login → admin dashboard → review queue → rejection without reason refused → rejection with reason →
   approval as planning reference → decision history → history
9. page reload restores the session from the refresh cookie
10. mobile viewport (390 px) dashboard

Screenshots were reviewed; layout defects found during this review (clipped table, legend overlap, number wrapping,
mobile overflow) were fixed and re-verified.

## 5. Defects found and fixed during verification

- Vite proxy could not reach the backend on Node 18 (`localhost` resolved to IPv6 `::1`); default changed to `127.0.0.1`.
- Refresh-token rotation logged users out when two page loads happened in quick succession (aborted response kept the
  old cookie); added a 30 s grace window for rotated tokens (not for logged-out tokens) + test.
- Pareto/ε bounds computed from rounded payoff values could be infeasible at the utopia point; raw values + tolerance.
- Duplicate-strategy detection compared investments only; now compares investments and dispatch.
- pandas 3 `NaN` handling in text columns of the legacy M1 schema.
- Trade-off messages flagged negligible (< 1 %) differences; significance threshold added.

## 6. Not tested

- **PowerShell scripts** (`scripts/*.ps1`) — no Windows/PowerShell available in the test environment. The equivalent
  steps were tested through `scripts/setup.sh` and manual commands. Please run `scripts\setup.ps1` once on Windows and
  report any issue.
- **Windows itself** (paths are relative and `pathlib`-based; no OS-specific code in the backend).
- **PostgreSQL** (`DATABASE_URL=postgresql+psycopg://...`) — only SQLite was tested.
- **Live LLM provider** (OpenRouter or other) — only the fake client and the HTTP error paths were tested.
- **Python 3.11 / 3.13** — only 3.12 was tested.
- On this Linux test machine the system Python lacked `ensurepip`, so clean virtual environments were created with
  `uv venv --seed` (pip inside the venv was then used normally). Standard Windows Python installers include `ensurepip`.
