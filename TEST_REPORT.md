# Test report

Executed on Linux (Ubuntu, Python 3.12.3, Node.js 18.19.1, Chromium via Playwright) on 2026-11-10.
Package versions resolved from `requirements.txt` at test time: FastAPI 0.162, Pydantic 2.14, pandas 3.1,
NumPy 2.5, Pyomo 6.10.1, highspy 1.15.1, LangGraph 1.2, SQLAlchemy 2.1.

## 1. Backend automated tests — `M4-Mahdi/tests` (pytest)

Result: **113 passed, 1 skipped, 0 failed** (≈ 85 s). The skipped test is the opt-in live LLM test (section 3).
Run three times: in the development folder, in a clean copy unzipped from `M4_Agentic_Energy_Planning.zip`
(after `scripts/setup.sh`), and in a clean copy of `M4-Mahdi-Backend.zip` with a fresh virtual environment.

| File | Tests | Covers |
|---|---|---|
| `test_data_loading.py` | 13 | prototype checks, duplicates, month format, intervals, missing months/columns, physical bounds, reference totals, unit conversions |
| `test_adapters.py` | 13 | MOCK/REAL detection, legacy schemas, multi-fold rows, M3 checks, manifest errors, 2024 reconciliation |
| `test_modeling.py` | 12 | demand pathway and extrapolation labels, unit conversions, retirements, simulation balance, capacity plans |
| `test_optimization.py` | 15 | long-term LP: constraints, infeasibility diagnosis, multi-objective methods, Pareto, solver failure |
| `test_operations.py` | 8 | **operational MILP**: schema validation, M1 demand + outages (units rounded up), optimal + integer + validated solution, minimum stable output, never worse than the status quo, LP relaxation ≤ MILP, infeasible reserve diagnosed, fuel limit → unserved energy reported (and infeasible when unserved energy is forbidden), four strategies distinct and validated, infeasible request reported |
| `test_scenarios_strategies.py` | 9 | scenario validation, predefined scenarios, long-term strategy generation |
| `test_agent.py` | 23 | rule parser for planning **and operational** requests, LLM values not stated in the text discarded (incl. outages / demand change), LLM failure fallback, HTTP errors and timeouts, JSON parsing, tools execute real logic, grounding check, planning workflow, **operational workflow end-to-end (clarification with M1-computed options → formulation checks → MILP strategies → recommendation → report)**, infeasibility reported before approval, LLM narrative accepted only when grounded, LLM client request body (fallback models, reasoning disabled), token usage records, empty answer and HTTP-200 provider errors, daily call budget, retry when reasoning is mandatory |
| `test_api.py` | 20 | auth, RBAC, tokens, admin endpoints, scenario CRUD, optimization endpoints, end-to-end planning + admin workflow, revision versions, **operational request via API → recommendation flag → requester's preferred option (others refused) → admin acceptance without replacing the planning reference**, **`POST /api/operations/optimize`**, agent info without secrets, **column migration of an old database**, **LLM usage ledger** |
| `test_live_llm.py` | 1 | live provider (skipped unless `M4_LIVE_LLM=1`) |

## 2. Frontend automated tests — `frontend/src/test` (Vitest)

Result: **19 passed, 0 failed** (development folder and clean copy). New: decision report (recommendation shown as
conditional, “prefer this option” calls the API with the right strategy, requester's choice badge, selection hidden
when not allowed), formulation checks rendering, single-choice clarification questions (option, custom percentage,
invalid text rejected). Type check (`tsc`, strict) and production build: **passed** (development folder and clean copy).

## 3. Live LLM (OpenRouter, free models) — executed, not part of the default suite

Configuration: `LLM_MODEL=nvidia/nemotron-3.5-super-vl:free`, fallbacks `nvidia/nemotron-3-ultra-550b-a55b:free,
openrouter/free`, reasoning disabled. Model availability was checked in the OpenRouter catalogue before selection;
`google/gemma-4-31b-it:free` returned HTTP 429 (rate-limited upstream) during selection and was not used.

| Run | Result |
|---|---|
| `scripts/check_llm.py` | key valid (free tier, 50 requests/day), the 3 models listed, JSON call OK, tool call OK (`get_reference_energy_system`) |
| `pytest tests/test_live_llm.py` (`M4_LIVE_LLM=1`) | **passed**: 3 calls (intent, tool selection, decision narrative), answered by 3 different free models through fallback routing; narrative accepted by the grounding check |
| Manual operational workflow | intent extracted by the LLM (objectives, “next month”, outage of 500 MW grounded in the text), 4 MILP strategies, LLM narrative passed the grounding check |
| Earlier attempt with reasoning enabled (effort low) | the model spent the 4,000-token budget on hidden reasoning and returned no text → reported as `empty answer`, template narrative used. Fixed by disabling reasoning (`LLM_REASONING_EFFORT=none`) and retrying with `low` when an endpoint makes reasoning mandatory (also observed live: HTTP 400 “Reasoning is mandatory”) |
| One live tool-selection call returned no tool call | the default investigation plan was used and a warning recorded (expected fallback) |

## 4. Browser end-to-end (Playwright, Chromium)

**Local, deterministic mode** (backend on 8000, Vite on 5173) — all steps passed, **0 console errors**:
planner login → agent → operational example request → clarification (M1 forecast / upper bound options with GWh,
gas units context) → answers → formulation with validation checklist and feasibility pre-check → approval →
operating strategies table + decision report with recommendation → “prefer this option” → full explanation and data
used → strategy page (schedule, dispatch & units, MILP validation incl. integrality) → admin review queue shows the
requester's choice → admin “Accept as operating plan” → operational run page → mobile (390 px) without horizontal
scroll. Screenshots were reviewed; the option cards were changed to one column after review.

**Production (http://129.151.225.146), live LLM** — passed, **0 console errors**: badge shows the free model and
calls today; the LLM interpreted the request (understanding card “LLM (validated)”), questions answered, approval,
decision report in ≈ 10 s, narrative written by a fallback free model and accepted by the grounding check, LLM usage
card shown, preferred option recorded.

Server checks after deployment: database created by the previous version migrated in place (new columns and
`llm_calls` table added, users kept); `/`, `/api/health` (`llm_configured: true`), `/agent` return 200; other sites
on the server still return 200.

## 5. Not tested

- **GitHub M1 / M2 / M3 implementations** — no repository containing them was available (see `IMPLEMENTATION_REPORT.md`);
  the adapters were tested with synthetic files in the expected schemas only.
- **PowerShell scripts** and **Windows** — no Windows machine; the equivalent `setup.sh` flow was tested on Linux.
- **PostgreSQL** — only SQLite; the column migration has a PostgreSQL branch that was not executed.
- **Python 3.11 / 3.13** — only 3.12.
- On this Linux machine the system Python lacks `ensurepip`; clean virtual environments were pre-created with
  `uv venv --seed`, then `setup.sh` / pip ran normally. Standard Windows Python installers include `ensurepip`.
