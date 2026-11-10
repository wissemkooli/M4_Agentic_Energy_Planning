# Team integration guide — M4-Mahdi

## 1. What M4-Mahdi contributes

`M4-Mahdi/` is the self-contained **backend** of module M4 (energy system simulation, planning and optimization).
It consumes the outputs of M1 (demand forecasts) and M3 (international benchmarks) through adapters and never
imports, executes or modifies M1/M3 code.

| Feature | Status |
|---|---|
| M1, M3, reference 2024 and technology-assumption adapters with validation and MOCK/REAL provenance | implemented, tested |
| Synthetic M1/M3 datasets (clearly labelled) for independent development | implemented |
| Monthly merit-order simulation | implemented, tested |
| Pyomo + HiGHS capacity-expansion LP; single, weighted-sum, ε-constraint, lexicographic, Pareto | implemented, tested |
| Short-term operational MILP (integer gas units in service, outages, fuel limit, reserve, unserved energy) with 4 alternative operating strategies | implemented, tested |
| Independent validation of solver results; infeasibility diagnosis | implemented, tested |
| 5 predefined scenarios + custom scenarios (stored, reproducible) | implemented, tested |
| Alternative strategy generation, roadmaps, trade-offs, risks | implemented, tested |
| LangGraph agent (planning and operational problems) with clarification, formulation checks, user approval, tool execution, recommendation under stated priorities, grounded decision report, requester's preferred option | implemented, tested (deterministic mode and fake LLM in the automated suite; live OpenRouter free models in opt-in runs) |
| OpenRouter integration: free model + fallbacks, reasoning control, daily call budget, token usage ledger | implemented, tested |
| Authentication (Argon2, JWT + refresh rotation), roles USER/ADMIN, admin approval workflow with audit trail | implemented, tested |
| FastAPI REST API + OpenAPI spec | implemented, tested |
| SQLite persistence (PostgreSQL via `DATABASE_URL`) | SQLite tested; PostgreSQL not tested |

## 2. Where to put it in the team repository

Work on your own branch; do not touch teammates' folders.

```powershell
cd <team-repo>
git checkout -b m4-mahdi            # or your existing M4 branch
# copy the folder(s) from the ZIP into the repository root, e.g.
#   <team-repo>/M4-Mahdi/           (backend — required)
#   <team-repo>/M4-frontend/        (optional: rename frontend/ from the full ZIP if you want the proposed UI)
git add M4-Mahdi
git status                          # verify that ONLY M4 files are staged
git commit -m "Add M4 backend (M4-Mahdi)"
git push -u origin m4-mahdi
```

Then open a pull request. Never force-push and never commit on teammates' branches.

M4-Mahdi already contains its own `.gitignore` (virtual environment, `.env`, database, caches). If the team
repository has a root `.gitignore`, nothing has to be changed there. No global configuration file is modified.

Naming: the Python package is `m4` (inside `M4-Mahdi/`), so it does not collide with packages named `app` or `src`
in other modules. All paths are relative to `M4-Mahdi/` and all configuration comes from `M4-Mahdi/.env`.

## 3. Dependencies

Python **3.12** recommended (3.11 and 3.13 are expected to work). Python 3.14/3.15 are not recommended yet:
binary wheels of `highspy` and `pydantic-core` may not be available for them. Keep your existing Python and create a
separate virtual environment:

```powershell
cd M4-Mahdi
py -3.12 -m venv .venv                 # install Python 3.12 first if needed: winget install Python.Python.3.12
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

`requirements.txt` = runtime; `requirements-dev.txt` adds pytest; `requirements-postgres.txt` adds the PostgreSQL driver.

## 4. Configure, initialise and launch

```powershell
Copy-Item .env.example .env            # then edit .env (JWT_SECRET_KEY, optional LLM key)
.\.venv\Scripts\python scripts\init_db.py
.\.venv\Scripts\python scripts\seed_demo.py        # development demo accounts
.\.venv\Scripts\python -m uvicorn main:app --reload --port 8000
```

Open http://127.0.0.1:8000/docs. Run the tests with `.\.venv\Scripts\python -m pytest`.
For the first real administrator use `scripts\create_admin.py` (prompts for credentials; nothing is hard-coded).

## 5. Using M4 without the proposed React frontend

The backend never imports frontend code. Any frontend (React, Vue, Streamlit, notebook, another service) can use:
- `docs/API.md` — endpoints, authentication, status values, examples;
- `docs/openapi.json` — machine-readable spec (generate typed clients from it);
- Swagger UI at `/docs`.

Typical flow: `POST /api/auth/login` → `POST /api/agent/requests` → answer questions → `POST .../approve` →
`GET /api/strategies?request_id=...` → admin `POST /api/admin/strategies/{id}/approve|reject|request-revision`.
Add the frontend origin to `CORS_ORIGINS`.

The engine can also be called directly from Python (no HTTP, no database):

```python
from m4.data_integration.repository import load_inputs
from m4.scenarios.predefined import predefined_scenarios
from m4.modeling.system import build_system_inputs
from m4.optimization.engine import run_optimization

bundle = load_inputs()
result = run_optimization(build_system_inputs(bundle, predefined_scenarios()["reference"]))
print(result["status"], result["results"]["summary"]["total_cost_npv_musd"])
```

## 6. Connecting the real M1 and M3 outputs

See `docs/DATA_INTEGRATION_GUIDE.md`. In short: copy the real CSVs into `data/input/m1/` and `data/input/m3/`
(do not move or edit the teammates' originals), then edit `data/input/sources.yml` (`path`, `data_status: REAL`,
`dataset_version`). Adapter changes are only needed if column names or formats differ from the agreed schemas.

Files to replace:

| File | Replace with |
|---|---|
| `data/input/m1/m1_forecast_synthetic.csv` | real M1 forecast output (schema `month, actual_gwh, forecast_gwh, lower_80, upper_80, lower_95, upper_95, model, horizon, fold, run_id, status`) |
| `data/input/m3/m3_benchmark_synthetic.csv` | real M3 benchmark output (17-column schema) |
| `data/input/reference_2024/reference_capacity_2024.csv` | documented 2024 reference system |
| `data/input/assumptions/*` | documented technology costs, emission factors, capacity-factor profiles, retirements, system parameters |

## 7. What remains mocked

Everything in `data/input/` is MOCK today: synthetic M1 forecasts (not XGBoost), synthetic M3 benchmark with fictional
comparators, fictional 2024 reference system, illustrative technology costs and system parameters, illustrative
targets in the predefined scenarios. Every API response and every frontend page shows the data status.

## 8. Compatibility recommendations for the team

- If the team standardises on another folder or package name, rename the folder only; the package `m4` and relative
  paths keep working.
- If M1/M3 publish their outputs to a shared location, point `sources.yml` paths there (relative to `data/input/`) or
  set `DATA_SOURCES_FILE` to a team-level manifest.
- If the team uses PostgreSQL, set `DATABASE_URL` and install `requirements-postgres.txt`.
