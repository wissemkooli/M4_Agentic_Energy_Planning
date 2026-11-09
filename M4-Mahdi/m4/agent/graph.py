"""LangGraph planning workflow.

                       ┌────────────── stage = "message" ──────────────┐
  START ─ route ──►  understand_request ─► check_completeness ─► investigate_data
                                                                      │
                          ┌── questions required ── ask_clarification ─► END (needs_clarification)
                          └── complete ─► build_scenario ─► formulate_problem ─► END (awaiting_approval)

                       ┌────────────── stage = "approved" (human approval) ─┐
  START ─ route ──►  simulate_baseline ─► optimize_strategies ─► validate_results
                     ─► analyze_tradeoffs ─► explain_strategies ─► END (strategies_ready)

The workflow state is persisted by the service layer between invocations, so the two
human gates (clarification/approval) survive server restarts. The LLM (when configured)
interprets requests, selects investigation tools and rephrases explanations; numerical
work is always done by the Python tools and the Pyomo/HiGHS optimizer. Optimization can
only start from the "approved" stage, which only the approval endpoint can set.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ..data_integration.repository import get_inputs
from ..modeling.system import ScenarioDataError, build_system_inputs
from ..modeling.demand import DemandPathwayError
from ..optimization.formulation import describe_formulation
from ..scenarios.schema import OBJECTIVE_LABELS, ScenarioConfig
from ..strategy.generator import STRATEGY_TEMPLATES, generate_strategies, select_strategy_keys
from .explain import explain_strategy
from .intent import PlanningIntent, interpret, merge_intents, parse_money_any
from .llm import LLMClient, LLMError
from .tools import MANDATORY_INVESTIGATION, TOOLS, call_tool


class PlanningState(TypedDict, total=False):
    stage: str
    user_message: str
    answers: dict
    intent: dict | None
    questions: list
    investigation: dict | None
    scenario: dict | None
    scenario_error: str | None
    formulation: dict | None
    strategy_plan: list
    assumptions_to_approve: list
    baseline_simulation: dict | None
    strategy_result: dict | None
    tradeoffs: dict | None
    trace: list
    messages: list
    status: str
    warnings: list
    llm: dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(state: PlanningState, node: str, summary: str, t0: float, tools: list[str] | None = None,
           status: str = "done") -> list:
    return list(state.get("trace", [])) + [{"node": node, "status": status, "summary": summary, "tools": tools or [],
                                            "seconds": round(time.perf_counter() - t0, 3), "at": _now()}]


def _msg(state: PlanningState, content: str, kind: str = "text", data: Any = None) -> list:
    return list(state.get("messages", [])) + [{"role": "assistant", "content": content, "kind": kind,
                                               "data": data, "at": _now()}]


def _client(config) -> LLMClient:
    return (config or {}).get("configurable", {}).get("llm") or LLMClient()


# ---------------------------------------------------------------------------- nodes
def route(state: PlanningState) -> PlanningState:
    return {}


def understand_request(state: PlanningState, config=None) -> PlanningState:
    t0 = time.perf_counter()
    client = _client(config)
    text = state.get("user_message") or ""
    previous = PlanningIntent.model_validate(state["intent"]) if state.get("intent") else None
    warnings = list(state.get("warnings", []))
    new, w = interpret(text, client) if text else (PlanningIntent(), [])
    warnings += w
    answers = dict(state.get("answers") or {})
    pending = {q["id"] for q in state.get("questions", [])}
    # a bare amount answering a pending budget question
    if text and "budget_total_musd" in pending and new.budget_total_musd is None and not new.budget_unlimited_confirmed:
        amount = parse_money_any(text)
        if amount is not None:
            new.budget_total_musd = amount
    intent = merge_intents(previous, new)
    for qid, value in answers.items():
        intent = _apply_answer(intent, qid, value, state.get("questions", []))
    summary = (f"Interpreted with {'LLM' if new.parser == 'llm' else 'deterministic parser'}: objectives "
               f"{intent.objectives or 'none'}; budget {intent.budget_total_musd or ('unlimited' if intent.budget_unlimited_confirmed else 'not set')}")
    return {"intent": intent.model_dump(), "warnings": warnings, "answers": {},
            "trace": _trace(state, "understand_request", summary, t0)}


def _apply_answer(intent: PlanningIntent, qid: str, value: Any, questions: list) -> PlanningIntent:
    q = next((x for x in questions if x["id"] == qid), None)
    if isinstance(value, str) and value.strip().lower() in {"accept", "accepted", "yes"} and q:
        value = q.get("proposed_value")
    if value is None or value == "":
        return intent
    if qid == "budget_total_musd":
        if str(value).strip().lower() in {"none", "unlimited", "no limit"}:
            intent.budget_unlimited_confirmed, intent.budget_total_musd = True, None
        else:
            intent.budget_total_musd = float(value)
        intent.budget_mentioned_without_value = False
    elif qid == "objectives":
        vals = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        intent.objectives = [v for v in vals if v in OBJECTIVE_LABELS]
        intent.primary_objective = intent.objectives[0] if intent.objectives else None
    elif qid == "reserve_margin_pct":
        intent.reserve_margin_pct = float(value)
    elif qid.startswith("target_year:"):
        _, field, idx = qid.split(":")
        items = getattr(intent, field)
        if int(idx) < len(items):
            items[int(idx)].year, items[int(idx)].year_inferred = int(value), False
    elif qid == "end_year":
        intent.end_year = int(value)
    return intent


def check_completeness(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    q: list[dict] = []
    if not intent.objectives:
        q.append({"id": "objectives", "required": True, "type": "multi_select",
                  "question": "Which objectives should the strategy pursue?",
                  "options": [{"value": k, "label": v} for k, v in OBJECTIVE_LABELS.items()],
                  "why": "No optimization objective could be identified in the request."})
    if intent.budget_mentioned_without_value:
        q.append({"id": "budget_total_musd", "required": True, "type": "number", "unit": "MUSD (constant 2024 USD)",
                  "question": "You mentioned a limited budget. What maximum total investment should apply over the horizon? "
                              "(answer 'unlimited' if there is no cap)",
                  "why": "A 'limited budget' has no mathematical meaning without a value; the agent will not choose it for you."})
    if intent.reliability_mentioned and intent.reserve_margin_pct is None:
        q.append({"id": "reserve_margin_pct", "required": True, "type": "number", "unit": "%",
                  "question": "Which reserve margin should define the reliability constraint?",
                  "proposed_value": None, "proposal_source": "system_parameters.yml default_reserve_margin (MOCK)",
                  "why": "Reliability was requested; it is modelled as firm capacity >= (1 + margin) x peak demand."})
    for field, label in (("renewable_targets", "renewable share target"),
                         ("emission_reduction_targets", "emission reduction target"),
                         ("gas_share_limits", "gas share limit")):
        for i, tgt in enumerate(getattr(intent, field)):
            if tgt.year is None or tgt.year_inferred:
                q.append({"id": f"target_year:{field}:{i}", "required": True, "type": "number", "unit": "year",
                          "question": f"From which year should the {label} of {tgt.value:g} % apply?",
                          "proposed_value": tgt.year, "why": "The year was not stated explicitly next to the target."})
    if intent.end_year is not None and intent.end_year <= 2024:
        q.append({"id": "end_year", "required": True, "type": "number", "unit": "year",
                  "question": "The planning horizon must end after 2024. Which final year should be used?",
                  "proposed_value": 2040, "why": "Invalid horizon."})
    return {"questions": q, "trace": _trace(state, "check_completeness",
                                            f"{len(q)} required clarification(s)" if q else "Request is complete", t0)}


DEFAULT_BENCHMARKS = {"max_renewables": ["renewable_share_electricity"],
                      "min_emissions": ["co2_intensity_electricity"],
                      "min_dependence": ["energy_import_dependence", "gas_share_electricity"]}


def _default_plan(intent: PlanningIntent) -> list[tuple[str, dict]]:
    h = {"start_year": intent.start_year or 2025, "end_year": intent.end_year or 2040}
    plan = [(n, h if n in ("get_demand_forecast", "check_data_quality") else {}) for n in MANDATORY_INVESTIGATION]
    inds = sorted({i for o in intent.objectives for i in DEFAULT_BENCHMARKS.get(o, [])}) or ["renewable_share_electricity"]
    plan.append(("get_international_benchmark", {"indicators": inds}))
    plan.append(("simulate_existing_fleet", h))
    if intent.budget_mentioned_without_value or intent.budget_total_musd is not None:
        plan.append(("estimate_unconstrained_investment", {**h, "reserve_margin_pct": intent.reserve_margin_pct}))
    return plan


def _llm_plan(intent: PlanningIntent, client: LLMClient) -> list[tuple[str, dict]]:
    tools = [t.schema() for t in TOOLS.values() if t.investigation]
    msg = client.chat([
        {"role": "system", "content": "You plan the data investigation for an electricity planning request. "
         "Call the tools needed to understand the system, demand, benchmarks, data quality and the "
         "information required to formulate the optimization problem. Do not answer in text."},
        {"role": "user", "content": json.dumps(intent.model_dump())}], tools=tools, max_tokens=600)
    calls = []
    for c in msg.get("tool_calls") or []:
        name = c.get("function", {}).get("name")
        try:
            args = json.loads(c.get("function", {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if name in TOOLS and TOOLS[name].investigation:
            calls.append((name, args if isinstance(args, dict) else {}))
    if not calls:
        raise LLMError("LLM selected no investigation tool.")
    return calls


def investigate_data(state: PlanningState, config=None) -> PlanningState:
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    client = _client(config)
    warnings = list(state.get("warnings", []))
    planner = "rules"
    if client.available:
        try:
            plan = _llm_plan(intent, client)
            planner = f"llm:{client.model}"
        except LLMError as exc:
            warnings.append(f"LLM tool planning failed ({exc}); default investigation plan used.")
            plan = _default_plan(intent)
    else:
        plan = _default_plan(intent)
    names = [n for n, _ in plan]
    h = {"start_year": intent.start_year or 2025, "end_year": intent.end_year or 2040}
    for mand in MANDATORY_INVESTIGATION:  # mandatory checks always run, whatever the planner chose
        if mand not in names:
            plan.append((mand, h if mand in ("get_demand_forecast", "check_data_quality") else {}))
    if intent.budget_mentioned_without_value and "estimate_unconstrained_investment" not in names:
        plan.append(("estimate_unconstrained_investment", h))

    bundle = get_inputs()
    results, calls = {}, []
    for name, args in plan:
        if name in results:
            continue
        try:
            results[name] = call_tool(bundle, name, args)
            calls.append({"tool": name, "args": args, "status": "ok"})
        except Exception as exc:  # tool errors are reported, never hidden
            results[name] = {"error": str(exc)}
            calls.append({"tool": name, "args": args, "status": "error", "error": str(exc)})
    findings = _findings(results)
    report = {"planner": planner, "tool_calls": calls, "findings": findings["findings"],
              "missing_information": findings["missing"], "required_assumptions": findings["assumptions"],
              "data_status": bundle.overall_status.value, "results": results}
    # enrich the budget question with a computed reference point
    questions = list(state.get("questions", []))
    ref = results.get("estimate_unconstrained_investment", {})
    for q in questions:
        if q["id"] == "budget_total_musd" and ref.get("status") == "optimal":
            q["context"] = (f"For reference, the least-cost plan without a budget cap requires "
                            f"{ref['total_investment_musd']:,.0f} MUSD of investment (computed by the optimizer, "
                            f"{bundle.overall_status.value} data).")
        if q["id"] == "reserve_margin_pct":
            q["proposed_value"] = round(bundle.assumptions.param("default_reserve_margin") * 100, 1)
    return {"investigation": report, "questions": questions, "warnings": warnings,
            "trace": _trace(state, "investigate_data", f"{len(calls)} tool calls (planner: {planner})", t0,
                            [c["tool"] for c in calls])}


def _findings(r: dict) -> dict:
    f, missing, assumptions = [], [], []
    ref = r.get("get_reference_energy_system", {})
    if "totals" in ref:
        f.append(f"2024 reference system ({ref['data_status']}): {ref['totals']['total_installed_capacity_mw']:,.0f} MW "
                 f"installed, {ref['totals']['total_available_capacity_mw']:,.0f} MW available, "
                 f"{ref['totals']['total_generation_gwh']:,.0f} GWh generated.")
    d = r.get("get_demand_forecast", {})
    if d.get("annual_forecast_gwh"):
        ys = sorted(d["annual_forecast_gwh"])
        a, b = d["annual_forecast_gwh"][ys[0]], d["annual_forecast_gwh"][ys[-1]]
        f.append(f"M1 demand forecast ({d['data_status']}, model label '{d['model_label']}'): {a:,.0f} GWh in {ys[0]} "
                 f"to {b:,.0f} GWh in {ys[-1]} ({(b / a - 1) * 100:+.0f} %).")
    sim = r.get("simulate_existing_fleet", {})
    if sim.get("summary"):
        if sim.get("first_year_with_unserved_energy"):
            f.append(f"Without new investment, demand cannot be fully supplied from {sim['first_year_with_unserved_energy']} "
                     f"({sim['summary']['total_unserved_gwh']:,.0f} GWh unserved over the horizon).")
        if sim.get("first_year_below_reserve_margin"):
            f.append(f"The existing fleet falls below a 15 % reserve margin from {sim['first_year_below_reserve_margin']}.")
    ui = r.get("estimate_unconstrained_investment", {})
    if ui.get("status") == "optimal":
        f.append(f"Least-cost plan without budget limit: {ui['total_investment_musd']:,.0f} MUSD of investment, "
                 f"{ui['renewable_share_final_year'] * 100:.1f} % renewables in the final year.")
    bm = r.get("get_international_benchmark", {})
    for ind, s in (bm.get("indicators") or {}).items():
        if s.get("tunisia_mean") is not None:
            f.append(f"M3 benchmark ({bm['data_status']}) {ind}: Tunisia {s['tunisia_mean']:.1f} {s['unit']} "
                     f"(rank {s['tunisia_rank']}/{s['n_countries']}), comparator median {s['peer_median']:.1f}.")
    q = r.get("check_data_quality", {})
    for issue in q.get("issues", []):
        if issue["level"] in ("error", "warning"):
            missing.append(f"[{issue['level']}] {issue['source']}: {issue['message']}")
    ta = r.get("get_technology_assumptions", {})
    if ta:
        assumptions.append(f"Technology costs, emission factors and capacity factors come from "
                           f"data/input/assumptions ({ta.get('data_status')}), not from M3.")
    assumptions.append("Emission reduction targets are measured against 2024 reference emissions computed from the "
                       "reference generation mix.")
    return {"findings": f, "missing": missing, "assumptions": assumptions}


def ask_clarification(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    qs = [q for q in state.get("questions", []) if q.get("required")]
    err = state.get("scenario_error")
    lines = ["Before formulating the optimization problem I need the following information:"]
    if err:
        lines = [f"The request cannot be formulated yet: {err}"]
    lines += [f"- {q['question']}" + (f" ({q['context']})" if q.get("context") else "") for q in qs]
    return {"status": "needs_clarification",
            "messages": _msg(state, "\n".join(lines), "questions", {"questions": qs,
                                                                   "findings": (state.get("investigation") or {}).get("findings", [])}),
            "trace": _trace(state, "ask_clarification", f"{len(qs)} question(s) sent to the user", t0)}


def build_scenario(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    i = PlanningIntent.model_validate(state["intent"])
    end = i.end_year or 2040
    start = i.start_year or 2025
    cfg = {
        "name": "Planning request: " + ", ".join(i.objectives),
        "description": state.get("user_message", "")[:500],
        "start_year": start, "end_year": end,
        "demand": {"basis": i.demand_case or "forecast", "efficiency_savings_pct_by_end": i.efficiency_savings_pct or 0.0},
        "constraints": {
            "budget_total_musd": i.budget_total_musd, "annual_budget_musd": i.annual_budget_musd,
            "renewable_share_targets": [{"year": t.year or end, "value": t.value} for t in i.renewable_targets],
            "emission_reduction_targets": [{"year": t.year or end, "value": t.value} for t in i.emission_reduction_targets],
            "max_gas_share": [{"year": t.year or end, "value": t.value} for t in i.gas_share_limits],
            "reserve_margin_pct": i.reserve_margin_pct, "max_import_share_pct": i.max_import_share_pct,
        },
        "objective": {"method": "single", "primary": i.primary_objective or "min_cost"},
    }
    try:
        sc = ScenarioConfig.model_validate(cfg)
        build_system_inputs(get_inputs(), sc)  # data consistency check (horizon vs M1, technologies ...)
    except ValidationError as exc:
        e = exc.errors()[0]
        msg = f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}"
        return {"scenario": None, "scenario_error": msg, "trace": _trace(state, "build_scenario", "invalid: " + msg, t0, status="error")}
    except (ScenarioDataError, DemandPathwayError) as exc:
        return {"scenario": None, "scenario_error": str(exc), "trace": _trace(state, "build_scenario", f"invalid: {exc}", t0, status="error")}
    return {"scenario": sc.model_dump(mode="json"), "scenario_error": None,
            "trace": _trace(state, "build_scenario", f"Scenario {start}-{end} built and validated", t0, ["create_scenario", "validate_scenario"])}


def formulate_problem(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    sc = ScenarioConfig.model_validate(state["scenario"])
    intent = PlanningIntent.model_validate(state["intent"])
    inp = build_system_inputs(get_inputs(), sc)
    form = describe_formulation(inp)
    keys = select_strategy_keys(intent.objectives)
    plan = [{"key": k, "name": STRATEGY_TEMPLATES[k]["name"], "objective": STRATEGY_TEMPLATES[k]["objective"],
             "method": STRATEGY_TEMPLATES[k]["config"](eps=0).method} for k in keys]
    assumptions = list(form["assumptions"]) + (state.get("investigation") or {}).get("required_assumptions", [])
    if intent.start_year is None or intent.end_year is None:
        assumptions.insert(0, f"Planning horizon {sc.start_year}-{sc.end_year}"
                              + (" (default)" if intent.end_year is None else " (end year from the request)") + ".")
    if intent.budget_total_musd is None and not intent.budget_unlimited_confirmed:
        assumptions.append("No total investment budget constraint (none was specified).")
    if inp.reserve_margin and intent.reserve_margin_pct is None:
        assumptions.append(f"Reserve margin {inp.reserve_margin * 100:.0f} % taken from the default system parameters (MOCK).")
    text = (f"Proposed formulation ({form['problem_class']}), horizon {form['horizon']}. "
            f"{len(plan)} alternative strategies will be computed with distinct formulations. "
            "Please review the constraints and assumptions, then approve or modify.")
    return {"formulation": form, "strategy_plan": plan, "assumptions_to_approve": assumptions,
            "status": "awaiting_approval",
            "messages": _msg(state, text, "formulation", {"formulation": form, "strategy_plan": plan,
                                                          "assumptions": assumptions, "intent": intent.model_dump(),
                                                          "notes": intent.notes, "warnings": state.get("warnings", [])}),
            "trace": _trace(state, "formulate_problem", "Formulation ready for human approval", t0,
                            ["formulate_optimization_problem"])}


def simulate_baseline(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    sc = state["scenario"]
    res = call_tool(get_inputs(), "simulate_existing_fleet",
                    {"start_year": sc["start_year"], "end_year": sc["end_year"], "demand_basis": sc["demand"]["basis"]})
    return {"baseline_simulation": res, "status": "optimizing",
            "trace": _trace(state, "simulate_baseline", "Existing-fleet simulation completed", t0, ["simulate_energy_system"])}


def optimize_strategies(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    sc = ScenarioConfig.model_validate(state["scenario"])
    intent = PlanningIntent.model_validate(state["intent"])
    out = generate_strategies(get_inputs(), sc, intent.objectives)
    n_solves = sum(len((s["run"].get("solver") or {}).get("solves", [])) for s in out["strategies"]) if out["strategies"] \
        else len((out.get("base_run") or {}).get("solver", {}).get("solves", []))
    return {"strategy_result": out,
            "trace": _trace(state, "optimize_strategies", f"{out['message']} ({n_solves} HiGHS solves)", t0,
                            ["run_optimization"] * max(1, len(out["strategies"])))}


def validate_results(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    out = state["strategy_result"]
    invalid = []
    for s in out["strategies"]:
        if s["feasible"] and s["run"]["validation"]["status"] != "valid":
            s["feasible"], s["status"] = False, "invalid"
            invalid.append(s["key"])
    summary = "All feasible strategies passed independent validation" if not invalid else f"Invalid: {invalid}"
    return {"strategy_result": out, "trace": _trace(state, "validate_results", summary, t0, ["analyze_optimization_results"])}


def analyze_tradeoffs(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    out = state["strategy_result"]
    rows = []
    for s in out["strategies"]:
        if not s["feasible"]:
            rows.append({"key": s["key"], "name": s["name"], "status": s["status"]})
            continue
        h = s["report"]["headline"]
        rows.append({"key": s["key"], "name": s["name"], "status": s["status"], "duplicate_of": s["duplicate_of"],
                     **{k: h[k] for k in ("total_cost_npv_musd", "total_investment_musd", "total_emissions_mt",
                                          "renewable_share_final_year", "gas_share_final_year",
                                          "dependence_share_horizon", "emission_change_vs_2024_pct")},
                     "implied_abatement_cost_usd_per_t": (s["report"].get("tradeoff_vs_least_cost") or {}).get(
                         "implied_abatement_cost_usd_per_t")})
    return {"tradeoffs": {"table": rows}, "trace": _trace(state, "analyze_tradeoffs", f"{len(rows)} strategies compared", t0,
                                                         ["compare_scenarios"])}


def explain_strategies(state: PlanningState, config=None) -> PlanningState:
    t0 = time.perf_counter()
    out = state["strategy_result"]
    client = _client(config)
    for s in out["strategies"]:
        s["explanation"] = explain_strategy(s, client) if s["feasible"] else None
    if out["status"] != "ok":
        diag = out.get("diagnostics") or {}
        text = "No feasible strategy exists under the approved constraints. " + (diag.get("summary") or out.get("message", ""))
        status, kind = "infeasible", "infeasible"
        data = {"diagnostics": diag}
    else:
        best = {}
        rows = [r for r in state["tradeoffs"]["table"] if r.get("total_cost_npv_musd") is not None and not r.get("duplicate_of")]
        if rows:
            best["cost"] = min(rows, key=lambda r: r["total_cost_npv_musd"])["name"]
            best["emissions"] = min(rows, key=lambda r: r["total_emissions_mt"])["name"]
        text = (f"{out['message']} Lowest cost: {best.get('cost')}. Lowest emissions: {best.get('emissions')}. "
                "All figures come from the HiGHS optimizer and passed independent validation. The strategies are now "
                "submitted to an administrator for review; no strategy is approved automatically.")
        status, kind = "strategies_ready", "results"
        data = {"tradeoffs": state["tradeoffs"]}
    return {"strategy_result": out, "status": status, "messages": _msg(state, text, kind, data),
            "trace": _trace(state, "explain_strategies", "Explanations generated from verified results", t0, ["explain_strategy"])}


# ---------------------------------------------------------------------------- graph
def _after_route(state: PlanningState) -> str:
    return "simulate_baseline" if state.get("stage") == "approved" else "understand_request"


def _after_investigation(state: PlanningState) -> str:
    return "ask_clarification" if any(q.get("required") for q in state.get("questions", [])) else "build_scenario"


def _after_build(state: PlanningState) -> str:
    return "ask_clarification" if state.get("scenario_error") else "formulate_problem"


def build_graph():
    g = StateGraph(PlanningState)
    for name, fn in [("route", route), ("understand_request", understand_request), ("check_completeness", check_completeness),
                     ("investigate_data", investigate_data), ("ask_clarification", ask_clarification),
                     ("build_scenario", build_scenario), ("formulate_problem", formulate_problem),
                     ("simulate_baseline", simulate_baseline), ("optimize_strategies", optimize_strategies),
                     ("validate_results", validate_results), ("analyze_tradeoffs", analyze_tradeoffs),
                     ("explain_strategies", explain_strategies)]:
        g.add_node(name, fn)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["understand_request", "simulate_baseline"])
    g.add_edge("understand_request", "check_completeness")
    g.add_edge("check_completeness", "investigate_data")
    g.add_conditional_edges("investigate_data", _after_investigation, ["ask_clarification", "build_scenario"])
    g.add_conditional_edges("build_scenario", _after_build, ["ask_clarification", "formulate_problem"])
    g.add_edge("ask_clarification", END)
    g.add_edge("formulate_problem", END)
    g.add_edge("simulate_baseline", "optimize_strategies")
    g.add_edge("optimize_strategies", "validate_results")
    g.add_edge("validate_results", "analyze_tradeoffs")
    g.add_edge("analyze_tradeoffs", "explain_strategies")
    g.add_edge("explain_strategies", END)
    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


GRAPH_NODES = ["understand_request", "check_completeness", "investigate_data", "ask_clarification", "build_scenario",
               "formulate_problem", "simulate_baseline", "optimize_strategies", "validate_results",
               "analyze_tradeoffs", "explain_strategies"]


def run_workflow(state: PlanningState, llm: LLMClient | None = None) -> PlanningState:
    cfg = {"configurable": {"llm": llm or LLMClient()}}
    result = get_graph().invoke(state, config=cfg)
    return result
