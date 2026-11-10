# M4 architecture

> Guiding principle: **the LLM reasons and orchestrates, Python simulates, the optimizer calculates,
> the human decides.**

## 1. Components

```
                 ┌────────────── React frontend (optional, separate app) ──────────────┐
                 │ login · dashboard · agent · scenarios · results · strategies · admin │
                 └──────────────────────────────┬───────────────────────────────────────┘
                                                │ REST/JSON (Bearer JWT)
┌───────────────────────────────────────────────▼───────────────────────────────────────────┐
│ FastAPI (m4/api)  auth · data · scenarios/optimization · agent · strategies · admin · history │
├───────────────┬───────────────────┬──────────────────────┬──────────────────┬──────────────┤
│ m4/auth       │ m4/services.py    │ m4/agent (LangGraph) │ m4/strategy      │ m4/db        │
│ Argon2, JWT,  │ orchestration +   │ intent · tools · LLM │ alternatives,    │ SQLAlchemy   │
│ RBAC deps     │ persistence       │ report · usage       │ roadmaps         │ SQLite/PG    │
├───────────────┴─────────┬─────────┴──────────┬───────────┴──────────────────┼──────────────┤
│ m4/optimization         │ m4/modeling        │ m4/operations                 │ m4/data_     │
│ long-term LP · HiGHS ·  │ demand pathway ·   │ short-term MILP (integer gas  │ integration  │
│ multi-objective ·       │ system inputs ·    │ units in service) · status    │ manifest ·   │
│ validation · diagnosis  │ simulation · KPIs  │ quo · strategies · validation │ adapters     │
└─────────────────────────┴────────────────────┴───────────────────────────────┴──────────────┘
                                   data/input (MOCK now, REAL later)
```

| Package | Responsibility | Inputs | Outputs |
|---|---|---|---|
| `data_integration` | Read files listed in `sources.yml`, validate, detect MOCK/REAL, normalize | CSV/YAML | `InputBundle` (demand, benchmark, reference, assumptions) with provenance |
| `scenarios` | Scenario schema (Pydantic) and the 5 predefined scenarios | user config | `ScenarioConfig` |
| `modeling` | Demand pathway, solver-independent `SystemInputs`, merit-order simulation, shared indicators | bundle + scenario | dispatch results, KPIs |
| `optimization` | Pyomo model, HiGHS solver wrapper, objectives, ε/weighted/lexicographic/Pareto, validation, infeasibility diagnosis, formulation text | `SystemInputs` | result dict (status, results, validation, diagnostics) |
| `strategy` | Alternative strategies from distinct formulations, roadmap, milestones, trade-offs, risks | base scenario + objectives | strategy reports |
| `operations` | Short-term operational problems: operate the existing fleet over 1–24 months. MILP with integer gas units in service, minimum stable output, reserve, imports, fuel limit, unserved energy at the value of lost load; status-quo simulation; alternative operating strategies; validation; infeasibility diagnosis | `OperationalProblem` + bundle | operating schedules and reports |
| `agent` | LangGraph workflow, tools, LLM client (OpenRouter, fallback models, usage ledger, daily budget), intent parsing, formulation checks, recommendation, decision report, grounded explanations | user messages, answers, approval | workflow state, strategies, decision report |
| `services` | Glue between API, engine and database | — | ORM rows / dicts |
| `db` | ORM models, sessions, init + seeding | — | users, tokens, scenarios, runs, requests, strategies, decisions |
| `auth` | Password hashing, tokens, role dependencies | credentials | `User` |
| `api` | HTTP layer, schemas, error mapping, CORS, OpenAPI | HTTP | JSON |

The engine (`modeling`, `optimization`, `strategy`) has no dependency on FastAPI or the database; it can be used
from a notebook or script.

## 2. Data flow

1. Adapters load M1, M3, reference and assumptions → `InputBundle` (cached; reloaded when files change).
2. A scenario + bundle → `build_system_inputs` → demand pathway (M1 + labelled extrapolation), existing capacity
   after retirements, technology parameters, resolved targets, assumptions list, provenance.
3. `simulate` or `run_optimization` consume `SystemInputs`; both use `compute_indicators`.
4. Results are validated, stored as `OptimizationRun` rows (with full scenario snapshot + provenance) and served.

## 3. Agent workflow (LangGraph)

Nodes (see `m4/agent/graph.py`): `understand_request → check_completeness → investigate_data →
(ask_clarification | build_scenario → formulate_problem → validate_formulation)` and, after human approval,
`simulate_baseline → optimize_strategies → validate_results → analyze_tradeoffs → explain_strategies →
build_decision_report`.

**Two problem types.** `understand_request` classifies the request (LLM or deterministic parser):

| | Long-term planning | Short-term operation |
|---|---|---|
| Example | “Reduce CO2 by 2035 with a limited budget” | “Demand increases next month, capacity is limited, minimize operating cost while satisfying demand” |
| Decisions | new capacity per technology and year, monthly dispatch | gas units kept in service (integer), monthly dispatch, imports, unserved energy |
| Model | LP (`m4/optimization`) | MILP (`m4/operations`) |
| Investigation tools | reference system, M1 annual demand, assumptions, data quality, M3 benchmark, existing-fleet simulation, least-cost reference | reference system, M1 monthly demand (+ interval, previous year), units available and maximum reserve achievable, status-quo operation, data quality |
| Strategies | cost-conscious, maximum decarbonization, balanced, energy security, renewable leadership | least-cost operation, reliability first, minimum gas consumption, balanced cost and fuel use |
| Admin acceptance | becomes the planning reference | recorded as the accepted operating plan (does not replace the planning reference) |

Relative periods (“next month”, “next summer”) are anchored on the M1 data (first forecast month after the last
actual observation), never on the server clock, and the interpretation is shown as an assumption to approve.

**Formulation checks before approval** (`validate_formulation`): decision variables have units; unit conversions
are consistent with the M1 file; every parameter is traced to a data source with its MOCK/REAL status; every requested
objective is optimized by at least one strategy; constraints are coherent; and a feasibility pre-check solves the
cost-minimizing problem once. If it is infeasible the elastic diagnosis (and, for operations, the highest reserve
margin achievable) is shown so the user can modify a constraint before approving. No result is shown before approval.

**Decision report** (`build_decision_report`, `m4/agent/report.py`): understanding, data used (source, status,
version), assumptions, formulation, what the solver computed, the distinct options with advantages/disadvantages,
a **recommendation conditional on the stated priorities** (e.g. balanced option when cost and emissions are both
requested, otherwise the best option on the first priority with explicit tie-breaks) and limitations. The narrative is
a deterministic template; when an LLM is configured it may rephrase it, and the text is accepted only if every number
appears in the verified facts.

- The workflow state is stored in `planning_requests.state` between invocations, so the human gates survive restarts.
- **Where the LLM is used** (if configured): interpreting the request into a structured intent (numbers must appear
  verbatim in the user's text, otherwise discarded); choosing investigation tools (mandatory checks always run; only
  read-only tools are allowed); rephrasing explanations (rejected if any number is not found in the verified results).
- **Where it is not used:** every numerical result, the scenario construction, the formulation, the optimization,
  validation and the trade-off table.
- Without an LLM, a deterministic parser and a default investigation plan are used; the workflow is identical.

Human gates:
1. **Clarification** — missing values (e.g. what “limited budget” or “limited production capacity” means) are asked,
   never invented; the agent shows computed reference points (least-cost investment without a budget, M1 forecast and
   upper bound of the month, gas units available, gas use of the status quo).
2. **Formulation approval (user)** — constraints, objective, alternative strategies, assumptions and the formulation
   checks are shown; optimization only starts after approval (only the approval endpoint can set the `approved` stage).
3. **Preferred option (user)** — the requester can mark the option they prefer (it may differ from the recommendation);
   this is recorded in the request and shown to the administrator.
4. **Strategy decision (administrator)** — accept, reject (reason required) or request a revision (the user modifies
   the request; a new strategy version is created; history is preserved). Nothing is executed on a real system.

**LLM provider.** OpenAI-compatible chat completions (OpenRouter by default) configured only in `M4-Mahdi/.env`:
`LLM_MODEL` (default `nvidia/nemotron-3.5-super-vl:free`), `LLM_FALLBACK_MODELS` (OpenRouter routing when the
primary free model is rate-limited), `LLM_REASONING_EFFORT` (`none` disables hidden reasoning of reasoning models;
the client retries once with `low` when an endpoint makes reasoning mandatory), `LLM_MAX_TOKENS`,
`LLM_DAILY_CALL_LIMIT` (free OpenRouter keys allow 50 requests/day; when the budget is reached the agent continues
deterministically). Every call (purpose, model that answered, tokens, latency, error) is recorded in the `llm_calls`
table (no prompt content) and per request; `/api/agent/info` and the admin overview show the usage. Per workflow
round the agent makes at most 3 LLM calls (intent, tool selection, narrative).

## 4. Persistence

SQLAlchemy 2 with SQLite by default (`data/m4.db`, no server needed) and PostgreSQL through `DATABASE_URL`.
Tables: `users`, `refresh_tokens`, `scenarios`, `optimization_runs` (kinds optimization, simulation, pareto,
operational), `planning_requests`, `strategies`, `strategy_decisions` (append-only audit), `llm_calls` (LLM usage
ledger). `python scripts/init_db.py` creates tables (idempotent) and loads the predefined scenarios. Alembic is not
set up; columns added after the first release (`strategies.problem_type`, `recommended`, `selected_by_requester`)
are added automatically at startup by `m4/db/session.py` (`ADDED_COLUMNS`), so existing databases keep their data.

## 5. Security

- Argon2id password hashing; minimum password policy (10 chars, letters + digits).
- Short-lived JWT access tokens (HS256) + opaque refresh tokens stored hashed, rotated on use (30 s grace for
  aborted browser responses), revoked on logout; httpOnly SameSite=Lax cookie for browsers.
- RBAC enforced with FastAPI dependencies; users only see their own runs, scenarios, requests and strategies.
- Secrets only in `.env` (never committed); in production `JWT_SECRET_KEY` is mandatory; the LLM key never leaves the backend.
- Not implemented (documented limitations): login rate limiting/lockout, CSRF tokens (mitigated by SameSite=Lax and
  Bearer tokens for state-changing calls), HTTPS termination (deployment concern).

## 6. Testing

`tests/` (pytest): data loading and validation, adapters, demand and unit conversion, simulation, optimization
(constraints, infeasibility, multi-objective, solver failure), operations (MILP integrality, minimum output, outages,
fuel limit and unserved energy, infeasibility diagnosis, strategies), scenarios and strategies, agent (parsers for
both problem types, LLM grounding/failure/fallback/usage/daily budget with a fake client, full workflows, decision
report), API (auth, RBAC, endpoints, end-to-end admin workflow, preferred-option selection, operations endpoint,
column migration, usage ledger). `tests/test_live_llm.py` calls the real provider and only runs with `M4_LIVE_LLM=1`.
The frontend has Vitest tests (form validation, guards, loading/error/empty states).
