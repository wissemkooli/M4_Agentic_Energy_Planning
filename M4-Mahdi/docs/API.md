# M4 REST API

Interactive documentation is served by FastAPI at **`/docs`** (Swagger UI) and **`/redoc`** when the backend runs.
The machine-readable specification is `docs/openapi.json` (regenerate with `python scripts/export_openapi.py`).

Base URL (local): `http://127.0.0.1:8000`. All payloads are JSON.

## Authentication

1. `POST /api/auth/login` with `{"login": "<username or email>", "password": "..."}` returns
   `access_token` (JWT, 30 min by default), `refresh_token` and `user`. The refresh token is **also** set as an
   httpOnly cookie `m4_refresh` (path `/api/auth`) for browsers.
2. Send `Authorization: Bearer <access_token>` on every other request.
3. `POST /api/auth/refresh` (cookie, or body `{"refresh_token": "..."}`) rotates the refresh token and returns a new access token.
4. `POST /api/auth/logout` revokes the refresh token.

Roles: `USER` and `ADMIN`. Role checks are enforced **on the backend**; admin routes return `403` for users.

## Errors

| Code | Meaning |
|---|---|
| 401 | missing/invalid/expired token or wrong credentials (`detail` explains which) |
| 403 | authenticated but not allowed (e.g. USER on `/api/admin/*`) |
| 404 | resource missing or not visible to the caller (other users' data are reported as 404) |
| 409 | invalid workflow transition (e.g. approving a request that is not awaiting approval, deciding twice) |
| 422 | validation error (Pydantic schema or scenario/data consistency, e.g. unknown technology, target outside horizon) |
| 503 | input data problem (missing/invalid input file) |
| 500 | unexpected error (details only in server logs) |

Error body: `{"detail": "<message>"}` or the FastAPI validation list.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | status, solver, data status, LLM configured (no secrets) |
| POST | `/api/auth/login` · `/refresh` · `/logout` | public | session management |
| GET | `/api/auth/me` | user | current user |
| GET | `/api/dashboard` | user | KPIs, reference system, demand outlook, benchmark summary, data quality, recent runs, approved reference |
| GET | `/api/energy/reference` | user | prepared 2024 reference system + provenance |
| GET | `/api/forecast?start=YYYY-MM&end=YYYY-MM` | user | normalized M1 monthly records |
| GET | `/api/benchmark[?indicator=]` | user | M3 table or one indicator summary |
| GET | `/api/data/sources` | user | provenance, data quality report, all technical assumptions |
| GET | `/api/scenarios` | user | predefined + own scenarios (admins: all) |
| POST | `/api/scenarios` | user | create a custom scenario `{"config": ScenarioConfig}` (201) |
| POST | `/api/scenarios/validate` | user | validate against schema **and** data; returns the formulation |
| GET | `/api/scenarios/meta` | user | objectives, technologies, methods, defaults |
| GET/DELETE | `/api/scenarios/{id}` | owner/admin | read / delete custom scenario |
| POST | `/api/optimization/run` | user | `{"scenario_id" \| "scenario", "objective"?}` → stored run with full result (201) |
| POST | `/api/simulation/run` | user | `{"scenario_id" \| "scenario", "capacity_plan"?: {tech: {year: MW}}}` |
| POST | `/api/optimization/pareto` | user | ε-constraint Pareto sweep `{"primary","secondary","points"}` |
| GET | `/api/optimization[?kind=]` | user | list of runs (own; admins: all) |
| GET | `/api/optimization/{run_id}` | owner/admin | run with full result |
| POST | `/api/comparison` | user | `{"run_ids": [...]}` side-by-side indicators |
| POST | `/api/operations/optimize` | user | solve one short-term operational problem (`OperationalProblem`, MILP) → stored run of kind `operational` (201) |
| GET | `/api/agent/info` | user | LLM mode, model and fallbacks, LLM usage today vs daily limit, problem types, workflow nodes, tool catalog, human gates |
| POST | `/api/agent/requests` | user | start a planning request `{"message"}` (201) |
| GET | `/api/agent/requests` | user | list requests |
| GET | `/api/agent/requests/{id}` | owner/admin | conversation, trace, `problem_type`, `understanding`, intent, questions, investigation, formulation, `formulation_validation`, trade-offs, `decision_report` (recommendation, narrative), `user_selection`, `llm_usage`, `llm_calls` |
| GET | `/api/agent/requests/{id}/investigation` | owner/admin | raw tool outputs of the investigation |
| POST | `/api/agent/requests/{id}/message` | owner/admin | `{"message"?, "answers"?: {question_id: value \| "accept"}}` |
| POST | `/api/agent/requests/{id}/approve` | owner/admin | `{"approve": bool, "comment"}` — human approval gate, runs optimization |
| POST | `/api/agent/requests/{id}/select` | owner/admin | `{"strategy_id", "comment"}` — the requester's preferred option (does not approve anything) |
| GET | `/api/strategies[?request_id=&status=]` | user | strategies (own; admins: all) |
| GET | `/api/strategies/reference` | user | current approved planning reference (or null) |
| GET | `/api/strategies/{id}` | owner/admin | report, roadmap, explanation, run validation, decisions, versions |
| GET | `/api/admin/overview` | admin | counts, recent decisions, reference, LLM usage (today, 7 days) |
| GET | `/api/admin/review-queue` | admin | strategies `pending_review` |
| POST | `/api/admin/strategies/{id}/approve` | admin | `{"comment"}` → approved; planning strategies become the planning reference, operational ones are recorded as the accepted operating plan |
| POST | `/api/admin/strategies/{id}/reject` | admin | `{"reason"}` (≥ 5 chars, mandatory) |
| POST | `/api/admin/strategies/{id}/request-revision` | admin | `{"comment"}` (≥ 5 chars) |
| GET | `/api/admin/decisions` | admin | append-only audit trail |
| GET/POST/PATCH | `/api/admin/users[/{id}]` | admin | list, create, change role / active flag |
| GET | `/api/history` | user | runs, requests, custom scenarios, decisions |

The suggested `POST /api/agent/message` and `POST /api/agent/approve` of the specification are implemented as
the resource-oriented `POST /api/agent/requests/{id}/message` and `/approve`, because a conversation is a
persistent planning request with its own state.

## Status values

- Run `status`: `optimal`, `infeasible`, `unbounded`, `time_limit`, `error`, `simulated`. `result.results` is
  `null` unless the status is `optimal` (optimization) or `simulated` — nothing is fabricated on failure.
- Planning request: `needs_clarification` → `awaiting_approval` → (`optimizing`) → `pending_review` | `infeasible`
  → `approved` | `rejected` | `revision_requested`; `formulation_rejected` if the user rejects the formulation.
- Strategy: `pending_review`, `approved`, `rejected`, `revision_requested`, `superseded` (a newer version exists),
  `duplicate` (numerically identical to another alternative; not reviewable), `infeasible`. Flags: `problem_type`
  (`planning` | `operational`), `recommended` (agent recommendation under the stated priorities),
  `selected_by_requester`.

Operational problem example (`POST /api/operations/optimize`):

```json
{"name": "January with an outage", "start_month": "2025-01", "months": 1,
 "demand_basis": "forecast", "demand_change_pct": 5,
 "capacity_outages": [{"technology": "gas", "unavailable_mw": 500}],
 "operating_reserve_pct": 10, "objective": {"method": "single", "primary": "min_operating_cost"}}
```

## Examples (PowerShell)

```powershell
$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/auth/login `
  -ContentType application/json -Body '{"login":"planner","password":"DemoPlanner-2026"}'
$h = @{ Authorization = "Bearer $($login.access_token)" }

# least-cost optimization of the predefined reference scenario
$run = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/optimization/run -Headers $h `
  -ContentType application/json -Body '{"scenario_id":"reference"}'
$run.status; $run.result.results.summary.total_cost_npv_musd

# agent workflow
$req = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/agent/requests -Headers $h `
  -ContentType application/json -Body '{"message":"Reduce CO2 emissions by 2035 with a budget of 7000 MUSD"}'
$req.status   # awaiting_approval (or needs_clarification)
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/agent/requests/$($req.id)/approve" -Headers $h `
  -ContentType application/json -Body '{"approve":true}'
```

The same with `curl`:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"login":"planner","password":"DemoPlanner-2026"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://127.0.0.1:8000/api/optimization/run -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"scenario_id":"stress"}'
```

## Building another frontend

Any client that can send HTTP requests can use M4: log in, keep the access token in memory, refresh it on 401,
and call the endpoints above. Browser clients on another origin must be listed in `CORS_ORIGINS`; to use the
refresh cookie cross-origin, serve over HTTPS and set `REFRESH_COOKIE_SECURE=true`, or use the `refresh_token`
returned in the login body.
