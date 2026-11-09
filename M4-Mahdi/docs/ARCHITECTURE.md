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
│ Argon2, JWT,  │ orchestration +   │ intent · tools ·     │ alternatives,    │ SQLAlchemy   │
│ RBAC deps     │ persistence       │ graph · explain      │ roadmaps         │ SQLite/PG    │
├───────────────┴─────────┬─────────┴──────────┬───────────┴──────────────────┴──────────────┤
│ m4/optimization         │ m4/modeling        │ m4/data_integration                         │
│ Pyomo LP · HiGHS ·      │ demand pathway ·   │ manifest · M1/M3/Reference/Assumptions      │
│ multi-objective ·       │ system inputs ·    │ adapters · validation · provenance ·        │
│ validation · diagnosis  │ simulation · KPIs  │ quality report                              │
└─────────────────────────┴────────────────────┴─────────────────────────────────────────────┘
                                   data/input (MOCK now, REAL later)
```

| Package | Responsibility | Inputs | Outputs |
|---|---|---|---|
| `data_integration` | Read files listed in `sources.yml`, validate, detect MOCK/REAL, normalize | CSV/YAML | `InputBundle` (demand, benchmark, reference, assumptions) with provenance |
| `scenarios` | Scenario schema (Pydantic) and the 5 predefined scenarios | user config | `ScenarioConfig` |
| `modeling` | Demand pathway, solver-independent `SystemInputs`, merit-order simulation, shared indicators | bundle + scenario | dispatch results, KPIs |
| `optimization` | Pyomo model, HiGHS solver wrapper, objectives, ε/weighted/lexicographic/Pareto, validation, infeasibility diagnosis, formulation text | `SystemInputs` | result dict (status, results, validation, diagnostics) |
| `strategy` | Alternative strategies from distinct formulations, roadmap, milestones, trade-offs, risks | base scenario + objectives | strategy reports |
| `agent` | LangGraph workflow, tools, LLM client, intent parsing, grounded explanations | user messages, answers, approval | workflow state, strategies |
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
(ask_clarification | build_scenario → formulate_problem)` and, after human approval,
`simulate_baseline → optimize_strategies → validate_results → analyze_tradeoffs → explain_strategies`.

- The workflow state is stored in `planning_requests.state` between invocations, so the human gates survive restarts.
- **Where the LLM is used** (if configured): interpreting the request into a structured intent (numbers must appear
  verbatim in the user's text, otherwise discarded); choosing investigation tools (mandatory checks always run; only
  read-only tools are allowed); rephrasing explanations (rejected if any number is not found in the verified results).
- **Where it is not used:** every numerical result, the scenario construction, the formulation, the optimization,
  validation and the trade-off table.
- Without an LLM, a deterministic parser and a default investigation plan are used; the workflow is identical.

Human gates:
1. **Clarification** — missing values (e.g. what “limited budget” means) are asked, never invented; the agent can
   show computed reference points (least-cost investment without a budget).
2. **Formulation approval (user)** — constraints, objective methods, alternative strategies and assumptions are shown;
   optimization only starts after approval (only the approval endpoint can set the `approved` stage).
3. **Strategy decision (administrator)** — accept (becomes the planning reference), reject (reason required) or
   request a revision (the user modifies the request; a new strategy version is created; history is preserved).

## 4. Persistence

SQLAlchemy 2 with SQLite by default (`data/m4.db`, no server needed) and PostgreSQL through `DATABASE_URL`.
Tables: `users`, `refresh_tokens`, `scenarios`, `optimization_runs`, `planning_requests`, `strategies`,
`strategy_decisions` (append-only audit). `python scripts/init_db.py` creates tables (idempotent) and loads the
predefined scenarios. Schema migrations (Alembic) are not set up; for schema changes in development, delete
`data/m4.db` and re-run `init_db.py`.

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
(constraints, infeasibility, multi-objective, solver failure), scenarios and strategies, agent (parser, LLM
grounding/failure with a fake client, full workflow), API (auth, RBAC, endpoints, end-to-end admin workflow).
The frontend has Vitest tests (form validation, guards, loading/error/empty states).
