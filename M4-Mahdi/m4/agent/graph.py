"""LangGraph planning workflow (long-term planning AND short-term operational problems).

                       ┌────────────── stage = "message" ──────────────┐
  START ─ route ──►  understand_request ─► check_completeness ─► investigate_data
                                                                      │
                          ┌── questions required ── ask_clarification ─► END (needs_clarification)
                          └── complete ─► build_scenario ─► formulate_problem ─► validate_formulation
                                                                                 ─► END (awaiting_approval)

                       ┌────────────── stage = "approved" (human approval) ─┐
  START ─ route ──►  simulate_baseline ─► optimize_strategies ─► validate_results
                     ─► analyze_tradeoffs ─► explain_strategies ─► build_decision_report ─► END (strategies_ready)

Problem types (decided in understand_request from the validated intent):
- planning:    capacity expansion 2025-2040 (LP, Pyomo/HiGHS) -> m4.scenarios / m4.optimization / m4.strategy
- operational: operate the existing fleet over 1-24 months (MILP with integer gas units in service)
               -> m4.operations

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
from ..operations.engine import run_operational, simulate_status_quo
from ..operations.formulation import describe_op_formulation
from ..operations.inputs import build_operational_inputs
from ..operations.schema import OP_OBJECTIVE_LABELS, OperationalProblem
from ..operations.strategies import OP_TEMPLATES, generate_operational_strategies, select_op_strategy_keys
from ..optimization.engine import run_optimization
from .explain import explain_strategy, template_explanation
from .intent import PlanningIntent, interpret, merge_intents, parse_money_any
from .llm import LLMClient, LLMError
from .report import build_decision_report, build_understanding, llm_narrative
from .tools import MANDATORY_INVESTIGATION, MANDATORY_OPERATIONAL, OPERATIONAL_TOOLS, PLANNING_ONLY_TOOLS, TOOLS, call_tool


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
    problem_type: str
    understanding: dict | None
    formulation_validation: dict | None
    decision_report: dict | None
    llm_calls: list


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


def _answered_by(client) -> str:
    """Model that actually answered the last call (OpenRouter may route to a fallback model)."""
    calls = getattr(client, "calls", None) or []
    return (calls[-1].get("model_used") if calls and isinstance(calls[-1], dict) else None) or client.model


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
    if intent.problem_type == "operational":
        _resolve_period(intent)
        summary = (f"Operational problem ({'LLM' if new.parser == 'llm' else 'deterministic parser'}): objectives "
                   f"{intent.op_objectives or 'none'}; period {intent.period_start} x {intent.period_months or 1} month(s)")
    else:
        summary = (f"Interpreted with {'LLM' if new.parser == 'llm' else 'deterministic parser'}: objectives "
                   f"{intent.objectives or 'none'}; budget {intent.budget_total_musd or ('unlimited' if intent.budget_unlimited_confirmed else 'not set')}")
    return {"intent": intent.model_dump(), "warnings": warnings, "answers": {}, "problem_type": intent.problem_type,
            "trace": _trace(state, "understand_request", summary, t0)}


def _resolve_period(intent: PlanningIntent) -> None:
    """Relative periods ('next month', 'next summer') are anchored on the M1 data, never on the server clock."""
    d = get_inputs().demand
    first = d.first_forecast_month
    if intent.period_start or not first:
        intent.period_months = intent.period_months or 1
        return
    fy, fm = int(first[:4]), int(first[5:])
    rel = intent.period_relative or "next month"
    if rel.startswith("next summer"):
        y = fy if fm <= 6 else fy + 1
        intent.period_start = f"{y}-06"
    elif rel.startswith("next winter"):
        y = fy if fm <= 12 else fy + 1
        intent.period_start = f"{y}-12"
    else:
        intent.period_start = first
    intent.period_months = intent.period_months or 1
    m = d.monthly
    last_actual = str(m[m["actual_gwh"].notna()]["month"].max()) if m["actual_gwh"].notna().any() else "n/a"
    note = (f"'{rel}' interpreted as starting {intent.period_start}: the first forecast month after the latest M1 "
            f"observation ({last_actual}), not the server date.")
    if note not in intent.notes:
        intent.notes.append(note)


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
    elif qid == "op_objectives":
        vals = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        intent.op_objectives = [v for v in vals if v in OP_OBJECTIVE_LABELS]
    elif qid == "demand_scenario":
        v = str(value).strip().lower()
        if v in {"forecast", "upper_80", "upper_95", "lower_80", "lower_95"}:
            intent.demand_case, intent.demand_change_pct = v, 0.0
        else:
            intent.demand_change_pct = float(v.rstrip("%"))
        intent.demand_increase_mentioned = False
    elif qid == "gas_unavailable_mw":
        intent.capacity_outages = [o for o in intent.capacity_outages if o.technology != "gas"]
        if float(value) > 0:
            from .intent import CapacityOutageSpec
            intent.capacity_outages.append(CapacityOutageSpec(technology="gas", unavailable_mw=float(value)))
        intent.capacity_limit_mentioned_without_value = False
    elif qid == "gas_limit_gwh":
        if str(value).strip().lower() in {"none", "unlimited", "no limit"}:
            intent.gas_limit_gwh = None
        else:
            intent.gas_limit_gwh = float(value)
        intent.gas_limit_mentioned_without_value = False
    elif qid == "period_months":
        intent.period_months = int(value)
    return intent


def _operational_questions(intent: PlanningIntent) -> list[dict]:
    q: list[dict] = []
    if not intent.op_objectives:
        q.append({"id": "op_objectives", "required": True, "type": "multi_select",
                  "question": "Which operating objectives matter, in order of priority?",
                  "options": [{"value": k, "label": v} for k, v in OP_OBJECTIVE_LABELS.items()],
                  "why": "No operating objective could be identified in the request."})
    if intent.demand_increase_mentioned and intent.demand_change_pct is None and intent.demand_case is None:
        q.append({"id": "demand_scenario", "required": True, "type": "single_select",
                  "question": "You expect demand to increase. Which demand should the operating plan cover?",
                  "options": [{"value": "forecast", "label": "M1 point forecast"},
                              {"value": "upper_80", "label": "M1 upper 80 % bound (stress case)"}],
                  "allow_number": True, "unit": "% change vs M1 forecast",
                  "why": "The M1 forecast already contains the expected growth; the agent will not choose a stress level for you."})
    if intent.capacity_limit_mentioned_without_value:
        q.append({"id": "gas_unavailable_mw", "required": True, "type": "number", "unit": "MW of gas capacity unavailable",
                  "question": "You mentioned limited production capacity. How many MW of gas capacity are unavailable "
                              "(maintenance, outages)? Answer 0 to use the available capacity of the reference data.",
                  "proposed_value": 0, "proposal_source": "reference 2024 availability",
                  "why": "A capacity limitation has no mathematical meaning without a value."})
    if intent.gas_limit_mentioned_without_value:
        q.append({"id": "gas_limit_gwh", "required": True, "type": "number", "unit": "GWh of gas-fired generation over the period",
                  "question": "You mentioned limited gas supply. What is the maximum gas-fired generation over the period? "
                              "(answer 'none' if there is no limit)",
                  "why": "The fuel limit must be quantified to become a constraint."})
    if intent.reliability_mentioned and intent.reserve_margin_pct is None:
        q.append({"id": "reserve_margin_pct", "required": True, "type": "number", "unit": "%",
                  "question": "Which operating reserve margin should apply (firm capacity in service above the monthly peak)?",
                  "proposed_value": None, "proposal_source": "system_parameters.yml operating_reserve_margin (MOCK)",
                  "why": "Reliability was requested; it is modelled as firm capacity in service >= (1 + margin) x peak."})
    if intent.period_start is None:
        q.append({"id": "period_months", "required": False, "type": "number", "unit": "months",
                  "question": "How many months should the operating plan cover?", "proposed_value": 1,
                  "why": "Default: one month."})
    return q


def check_completeness(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    if intent.problem_type == "operational":
        q = _operational_questions(intent)
        return {"questions": q, "understanding": build_understanding(intent.model_dump(), q),
                "trace": _trace(state, "check_completeness",
                                f"{sum(1 for x in q if x.get('required'))} required clarification(s)" if q else
                                "Operational request is complete", t0)}
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
    return {"questions": q, "understanding": build_understanding(intent.model_dump(), q),
            "trace": _trace(state, "check_completeness", f"{len(q)} required clarification(s)" if q else "Request is complete", t0)}


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


def _op_args(intent: PlanningIntent) -> dict:
    """Arguments of operational tools come from the validated intent (never from free LLM text)."""
    outages = [{"technology": o.technology, "unavailable_mw": o.unavailable_mw} for o in intent.capacity_outages
               if o.unavailable_mw is not None]
    return {"start_month": intent.period_start, "months": intent.period_months or 1,
            "demand_change_pct": intent.demand_change_pct or 0.0, "capacity_outages": outages,
            "demand_basis": intent.demand_case or "forecast",
            "operating_reserve_pct": intent.reserve_margin_pct, "gas_generation_limit_gwh": intent.gas_limit_gwh}


def _default_op_plan(intent: PlanningIntent) -> list[tuple[str, dict]]:
    a = _op_args(intent)
    return [(n, a if n in OPERATIONAL_TOOLS else ({"start_year": int(a["start_month"][:4]), "end_year": int(a["start_month"][:4])}
                                                  if n == "check_data_quality" else {})) for n in MANDATORY_OPERATIONAL]


def _llm_plan(intent: PlanningIntent, client: LLMClient) -> list[tuple[str, dict]]:
    op = intent.problem_type == "operational"
    allowed = [t for t in TOOLS.values() if t.investigation and not (op and t.name in PLANNING_ONLY_TOOLS)
               and not (not op and t.name in OPERATIONAL_TOOLS)]
    kind = ("a SHORT-TERM OPERATIONAL request (operate the existing fleet over a few months)" if op
            else "a LONG-TERM electricity planning request")
    msg = client.chat([
        {"role": "system", "content": f"You plan the data investigation for {kind}. Call the tools needed to understand "
         "the system, demand, data quality and the information required to formulate the optimization problem. "
         "Do not answer in text."},
        {"role": "user", "content": json.dumps(intent.model_dump())}], tools=[t.schema() for t in allowed],
        purpose="tool_selection")
    calls = []
    for c in msg.get("tool_calls") or []:
        name = c.get("function", {}).get("name")
        try:
            args = json.loads(c.get("function", {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if name in TOOLS and TOOLS[name].investigation and any(t.name == name for t in allowed):
            args = args if isinstance(args, dict) else {}
            if name in OPERATIONAL_TOOLS:
                args = {**args, **_op_args(intent)}
            calls.append((name, args))
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
            if intent.problem_type == "operational":
                plan = [(n, a) for n, a in plan if n not in PLANNING_ONLY_TOOLS]
            planner = f"llm:{_answered_by(client)}"
        except LLMError as exc:
            warnings.append(f"LLM tool planning failed ({exc}); default investigation plan used.")
            plan = _default_op_plan(intent) if intent.problem_type == "operational" else _default_plan(intent)
    else:
        plan = _default_op_plan(intent) if intent.problem_type == "operational" else _default_plan(intent)
    names = [n for n, _ in plan]
    op = intent.problem_type == "operational"
    if op:
        for n, a in _default_op_plan(intent):  # mandatory checks always run, whatever the planner chose
            if n not in names:
                plan.append((n, a))
    else:
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
    findings = _op_findings(results) if op else _findings(results)
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
            q["proposed_value"] = round(bundle.assumptions.param("operating_reserve_margin" if op else "default_reserve_margin") * 100, 1)
            fleet = results.get("get_operational_fleet", {})
            if op and "max_achievable_reserve_margin" in fleet:
                q["context"] = (f"With every available unit in service the highest reserve margin achievable in all months "
                                f"is {fleet['max_achievable_reserve_margin'] * 100:.1f} % (computed from the fleet data).")
        if q["id"] == "demand_scenario":
            md = results.get("get_monthly_demand", {}).get("months", [])
            fc = sum(m.get("forecast_gwh", 0) for m in md)
            up = sum(m.get("upper_80", 0) for m in md) if all("upper_80" in m for m in md) else None
            q["options"] = [{"value": "forecast", "label": f"M1 point forecast ({fc:,.0f} GWh over the period)"}] + (
                [{"value": "upper_80", "label": f"M1 upper 80 % bound ({up:,.0f} GWh, stress case)"}] if up else [])
            yoy = [m["change_vs_previous_year_pct"] for m in md if m.get("change_vs_previous_year_pct") is not None]
            if yoy:
                rng = f"{min(yoy):+.1f} %" if abs(max(yoy) - min(yoy)) < 0.05 else f"{min(yoy):+.1f} % to {max(yoy):+.1f} %"
                q["context"] = (f"The M1 forecast already includes a change of {rng} versus the same month one year earlier. "
                                "You can also enter your own % change instead.")
        if q["id"] == "gas_unavailable_mw":
            fleet = results.get("get_operational_fleet", {})
            if fleet.get("months"):
                f0 = fleet["months"][0]
                q["context"] = (f"Reference data: {f0['units_total'].get('gas', 0)} gas units of {fleet['unit_size_mw']:,.0f} MW "
                                f"({f0['capacity_in_service_mw'].get('gas', 0):,.0f} MW installed).")
        if q["id"] == "gas_limit_gwh":
            sq = results.get("simulate_status_quo_operation", {})
            if sq.get("summary"):
                q["context"] = f"Operating with every unit in service would use {sq['summary']['gas_gwh']:,.0f} GWh of gas-fired generation."
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


def _op_findings(r: dict) -> dict:
    f, missing, assumptions = [], [], []
    ref = r.get("get_reference_energy_system", {})
    if "totals" in ref:
        f.append(f"Reference fleet ({ref['data_status']}): {ref['totals']['total_installed_capacity_mw']:,.0f} MW installed, "
                 f"{ref['totals']['total_available_capacity_mw']:,.0f} MW available on average.")
    md = r.get("get_monthly_demand", {})
    for m in md.get("months", []):
        if m.get("available"):
            txt = f"M1 demand {m['month']} ({md['data_status']}): {m['forecast_gwh']:,.0f} GWh"
            if m.get("lower_80") is not None:
                txt += f" (80 % interval {m['lower_80']:,.0f}-{m['upper_80']:,.0f})"
            if m.get("change_vs_previous_year_pct") is not None:
                txt += f", {m['change_vs_previous_year_pct']:+.1f} % vs one year earlier"
            f.append(txt + ".")
        else:
            missing.append(f"[error] M1: no demand value for {m['month']}.")
    fl = r.get("get_operational_fleet", {})
    if fl.get("months"):
        f0 = fl["months"][0]
        f.append(f"Gas units available: {f0['units_available']} of {f0['units_total']} ({fl['unit_size_mw']:,.0f} MW each); "
                 f"highest reserve margin achievable with every unit in service: {fl['max_achievable_reserve_margin'] * 100:.1f} % "
                 f"(required by default: {fl['required_reserve_margin'] * 100:.0f} %).")
    sq = r.get("simulate_status_quo_operation", {})
    if sq.get("summary"):
        s = sq["summary"]
        f.append(f"Status quo (every unit in service, merit order): operating cost {s['operating_cost_musd']:,.1f} MUSD, "
                 f"gas {s['gas_gwh']:,.0f} GWh, unserved {s['unserved_gwh']:,.1f} GWh.")
        missing += [f"[warning] status quo: {i}" for i in sq.get("issues", [])[:4]]
    q = r.get("check_data_quality", {})
    for issue in q.get("issues", []):
        if issue["level"] in ("error", "warning"):
            missing.append(f"[{issue['level']}] {issue['source']}: {issue['message']}")
    assumptions.append("Unit size, minimum stable output, monthly fixed cost per unit and value of lost load come from "
                       "data/input/assumptions/system_parameters.yml (MOCK).")
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


def _build_operational(state: PlanningState, i: PlanningIntent, t0: float) -> PlanningState:
    bundle = get_inputs()
    ref = bundle.reference.technologies.set_index("technology")
    outages = []
    for o in i.capacity_outages:
        mw = o.unavailable_mw
        if mw is None and o.reduction_pct is not None and o.technology in ref.index:
            mw = float(ref.loc[o.technology, "installed_capacity_mw"]) * o.reduction_pct / 100.0
        if mw is not None:
            outages.append({"technology": o.technology, "unavailable_mw": round(mw, 3)})
    labels = [OP_OBJECTIVE_LABELS[o].split(":")[0].split(" (")[0] for o in i.op_objectives] or ["operating cost"]
    cfg = {"name": f"Operational request {i.period_start}: " + ", ".join(i.op_objectives or ["min_operating_cost"]),
           "description": state.get("user_message", "")[:500], "start_month": i.period_start, "months": i.period_months or 1,
           "demand_basis": i.demand_case or "forecast", "demand_change_pct": i.demand_change_pct or 0.0,
           "capacity_outages": outages, "gas_generation_limit_gwh": i.gas_limit_gwh,
           "max_import_share_pct": i.max_import_share_pct, "operating_reserve_pct": i.reserve_margin_pct,
           "objective": {"method": "single", "primary": (i.op_objectives or ["min_operating_cost"])[0]}}
    try:
        pr = OperationalProblem.model_validate(cfg)
        build_operational_inputs(bundle, pr)
    except ValidationError as exc:
        e = exc.errors()[0]
        msg = f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}"
        return {"scenario": None, "scenario_error": msg, "trace": _trace(state, "build_scenario", "invalid: " + msg, t0, status="error")}
    except (ScenarioDataError, DemandPathwayError) as exc:
        return {"scenario": None, "scenario_error": str(exc), "trace": _trace(state, "build_scenario", f"invalid: {exc}", t0, status="error")}
    return {"scenario": pr.model_dump(mode="json"), "scenario_error": None,
            "trace": _trace(state, "build_scenario", f"Operational problem {pr.start_month} x {pr.months} month(s) built and "
                                                     f"validated ({', '.join(labels)})", t0, ["create_operational_problem"])}


def build_scenario(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    i = PlanningIntent.model_validate(state["intent"])
    if i.problem_type == "operational":
        return _build_operational(state, i, t0)
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


def _formulate_operational(state: PlanningState, intent: PlanningIntent, t0: float) -> PlanningState:
    pr = OperationalProblem.model_validate(state["scenario"])
    inp = build_operational_inputs(get_inputs(), pr)
    form = describe_op_formulation(inp)
    keys = select_op_strategy_keys(intent.op_objectives)
    methods = {"least_cost_operations": "single", "reliability_first": "lexicographic", "min_gas_use": "lexicographic",
               "balanced_operations": "epsilon_constraint"}
    plan = [{"key": k, "name": OP_TEMPLATES[k]["name"], "objective": OP_TEMPLATES[k]["objective"], "method": methods[k]}
            for k in keys]
    assumptions = list(intent.notes) + list(form["assumptions"]) + (state.get("investigation") or {}).get("required_assumptions", [])
    if intent.reserve_margin_pct is None:
        assumptions.append(f"Operating reserve margin {inp.reserve_margin * 100:.0f} % taken from the default system parameters (MOCK).")
    return {"formulation": form, "strategy_plan": plan, "assumptions_to_approve": list(dict.fromkeys(assumptions)),
            "trace": _trace(state, "formulate_problem", f"MILP formulation with {len(plan)} candidate strategies", t0,
                            ["formulate_optimization_problem"])}


def formulate_problem(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    if intent.problem_type == "operational":
        return _formulate_operational(state, intent, t0)
    sc = ScenarioConfig.model_validate(state["scenario"])
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
    return {"formulation": form, "strategy_plan": plan, "assumptions_to_approve": assumptions,
            "trace": _trace(state, "formulate_problem", f"LP formulation with {len(plan)} candidate strategies", t0,
                            ["formulate_optimization_problem"])}


def validate_formulation(state: PlanningState) -> PlanningState:
    """Checks before human approval: variables/units, data provenance, objective coverage, coherence, feasibility."""
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    op = intent.problem_type == "operational"
    form = state["formulation"]
    bundle = get_inputs()
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    missing_units = [v["symbol"] for v in form["decision_variables"] if not v.get("unit")]
    add("Decision variables defined with units", "fail" if missing_units else "pass",
        "; ".join(f"{v['symbol']} [{v['unit']}] x {v['count']}" for v in form["decision_variables"]))
    if op:
        inp = build_operational_inputs(bundle, OperationalProblem.model_validate(state["scenario"]))
        loss = bundle.assumptions.param("network_loss_factor")
        col = "forecast_gwh" if inp.problem.demand_basis == "forecast" else inp.problem.demand_basis
        mon = bundle.demand.monthly.set_index("month")
        m1_periods = [p for p in inp.periods if inp.demand_source[p] == "M1_forecast"]
        resid = max((abs(inp.demand_m1_gwh[p] - float(mon.loc[inp.labels[p], col]) * (1 + loss)) for p in m1_periods), default=0.0)
        unit_ok = all(abs(inp.unit_output_gwh(t, p) - inp.unit_size_mw * inp.techs[t].availability * inp.hours[p] / 1000) < 1e-9
                      for t in inp.dispatchable for p in inp.periods)
        add("Units consistent (MW x h / 1000 = GWh; USD/MWh x GWh / 1000 = MUSD)", "pass" if resid < 1e-6 and unit_ok else "fail",
            f"monthly demand matches the M1 file (max residual {resid:.2e} GWh); unit energy = size x availability x hours / 1000")
    else:
        inp = build_system_inputs(bundle, ScenarioConfig.model_validate(state["scenario"]))
        m1 = [(y, m) for (y, m) in inp.demand_gwh if inp.demand.set_index(["year", "month"]).loc[(y, m), "source"] == "M1_forecast"]
        add("Units consistent (MW x h / 1000 = GWh; USD/MWh x GWh / 1000 = MUSD)", "pass",
            f"{len(inp.demand_gwh)} monthly demand values in GWh ({len(m1)} from M1, the rest labelled M4 extrapolation); "
            "capacities in MW, costs in MUSD")
    prov = inp.provenance
    status = {p["source"]: p["data_status"] for p in prov}
    add("Parameters traced to data sources", "warn" if any(v != "REAL" for v in status.values()) else "pass",
        "; ".join(f"{k}: {v}" for k, v in status.items()) + " — every parameter comes from data/input (see provenance)")
    requested = intent.op_objectives if op else intent.objectives
    covered = {"min_operating_cost", "min_unserved", "min_gas", "min_emissions"} if op else \
        {"min_cost", "min_emissions"} | ({"min_dependence"} if "min_dependence" in requested else set()) | \
        ({"max_renewables"} if "max_renewables" in requested else set())
    missing_obj = [o for o in requested if o not in covered]
    add("Objective reflects the request", "warn" if missing_obj or not requested else "pass",
        (f"requested: {requested or 'none (default cost minimization)'}; each is optimized by at least one strategy"
         + (f"; not covered: {missing_obj}" if missing_obj else "")))
    issues = []
    if op:
        for o in inp.problem.capacity_outages:
            inst = max(inp.installed_mw[(o.technology, p)] for p in inp.periods)
            if o.unavailable_mw > inst:
                issues.append(f"{o.unavailable_mw:,.0f} MW of {o.technology} unavailable exceeds the installed {inst:,.0f} MW")
        if inp.gas_limit_gwh is not None and inp.gas_limit_gwh == 0:
            issues.append("gas-fired generation limited to 0 GWh")
    else:
        hydro_like = [t for t, x in inp.techs.items() if x.is_renewable and x.category != "variable"]
        for y, sh in inp.re_min_share.items():
            ceiling = inp.max_vre_share + sum(1 for _ in hydro_like) * 0.05
            if sh > min(1.0, ceiling):
                issues.append(f"renewable target {sh * 100:.0f} % in {y} exceeds the variable-renewable integration limit "
                              f"({inp.max_vre_share * 100:.0f} %) plus the small hydro potential")
    add("Constraints coherent", "warn" if issues else "pass", "; ".join(issues) or "no contradictory or out-of-range constraint detected")
    # feasibility pre-check: one solve of the cost-minimizing problem (numbers are not reported before approval)
    if op:
        pre = run_operational(inp, diagnose=True)
    else:
        pre = run_optimization(inp, diagnose=True)
    feas = {"feasible": pre["feasible"], "status": pre["status"], "solver_seconds": round(sum(x["seconds"] for x in pre["solver"]["solves"]), 3)}
    if pre["feasible"]:
        add("Feasibility pre-check", "pass", "the approved constraints admit at least one solution (HiGHS)")
    else:
        feas["diagnostics"] = pre.get("diagnostics")
        hint = ""
        if op:
            fl = call_tool(bundle, "get_operational_fleet", _op_args(intent))
            hint = (f" The highest reserve margin achievable in every month with all available units is "
                    f"{fl['max_achievable_reserve_margin'] * 100:.1f} %.")
            feas["max_achievable_reserve_margin"] = fl["max_achievable_reserve_margin"]
        add("Feasibility pre-check", "fail", (pre.get("message") or "infeasible") + hint +
            " You can modify a constraint before approving, or approve to record the diagnosis.")
    overall = "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "pass")
    validation = {"status": overall, "checks": checks, "feasibility": feas}
    plan = state["strategy_plan"]
    text = (f"Proposed formulation ({form['problem_class']}), {'period' if op else 'horizon'} {form['horizon']}. "
            f"{len(plan)} alternative strategies will be computed with distinct formulations. "
            + ("Pre-check: the problem is feasible. " if feas["feasible"] else
               "Pre-check: the problem is INFEASIBLE as stated — see the validation below. ")
            + "Please review the constraints and assumptions, then approve or modify.")
    return {"formulation_validation": validation, "status": "awaiting_approval",
            "messages": _msg(state, text, "formulation", {"formulation": form, "strategy_plan": plan,
                                                          "assumptions": state.get("assumptions_to_approve", []),
                                                          "intent": intent.model_dump(), "notes": intent.notes,
                                                          "warnings": state.get("warnings", []), "validation": validation,
                                                          "understanding": state.get("understanding")}),
            "trace": _trace(state, "validate_formulation", f"Formulation checks: {overall}; feasible: {feas['feasible']}", t0,
                            ["validate_formulation"])}


def simulate_baseline(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    sc = state["scenario"]
    if state.get("problem_type") == "operational":
        inp = build_operational_inputs(get_inputs(), OperationalProblem.model_validate(sc))
        res = simulate_status_quo(inp)
        return {"baseline_simulation": {"method": res["method"], "summary": res["summary"], "issues": res["issues"]},
                "status": "optimizing",
                "trace": _trace(state, "simulate_baseline", "Status-quo operation simulated (every unit in service)", t0,
                                ["simulate_status_quo_operation"])}
    res = call_tool(get_inputs(), "simulate_existing_fleet",
                    {"start_year": sc["start_year"], "end_year": sc["end_year"], "demand_basis": sc["demand"]["basis"]})
    return {"baseline_simulation": res, "status": "optimizing",
            "trace": _trace(state, "simulate_baseline", "Existing-fleet simulation completed", t0, ["simulate_energy_system"])}


def optimize_strategies(state: PlanningState) -> PlanningState:
    t0 = time.perf_counter()
    intent = PlanningIntent.model_validate(state["intent"])
    if state.get("problem_type") == "operational":
        out = generate_operational_strategies(get_inputs(), OperationalProblem.model_validate(state["scenario"]), intent.op_objectives)
    else:
        sc = ScenarioConfig.model_validate(state["scenario"])
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
        if h.get("kind") == "operational":
            rows.append({"key": s["key"], "name": s["name"], "status": s["status"], "duplicate_of": s["duplicate_of"],
                         **{k: h[k] for k in ("operating_cost_musd", "unserved_gwh", "gas_gwh", "imports_gwh", "emissions_mt",
                                              "min_reserve_margin", "avg_units_in_service")},
                         "additional_cost_musd": (s["report"].get("tradeoff_vs_least_cost") or {}).get("additional_operating_cost_musd")})
            continue
        rows.append({"key": s["key"], "name": s["name"], "status": s["status"], "duplicate_of": s["duplicate_of"],
                     **{k: h[k] for k in ("total_cost_npv_musd", "total_investment_musd", "total_emissions_mt",
                                          "renewable_share_final_year", "gas_share_final_year",
                                          "dependence_share_horizon", "emission_change_vs_2024_pct")},
                     "implied_abatement_cost_usd_per_t": (s["report"].get("tradeoff_vs_least_cost") or {}).get(
                         "implied_abatement_cost_usd_per_t")})
    return {"tradeoffs": {"table": rows, "problem_type": state.get("problem_type", "planning")}, "trace": _trace(state, "analyze_tradeoffs", f"{len(rows)} strategies compared", t0,
                                                         ["compare_scenarios"])}


def explain_strategies(state: PlanningState, config=None) -> PlanningState:
    """Per-strategy explanations from verified results (deterministic templates; no LLM call per strategy)."""
    t0 = time.perf_counter()
    out = state["strategy_result"]
    for s in out["strategies"]:
        s["explanation"] = template_explanation(s) if s["feasible"] else None
    return {"strategy_result": out, "trace": _trace(state, "explain_strategies",
                                                    "Explanations generated from verified results", t0, ["explain_strategy"])}


def build_decision_report_node(state: PlanningState, config=None) -> PlanningState:
    """Recommendation under the stated priorities + human-readable report; the LLM may rephrase it (grounded)."""
    t0 = time.perf_counter()
    out = state["strategy_result"]
    client = _client(config)
    warnings = list(state.get("warnings", []))
    report = build_decision_report(dict(state))
    if out["status"] == "ok" and client.available:
        text, err = llm_narrative(report, client)
        if text:
            report["narrative"] = {"source": f"llm:{_answered_by(client)} (grounding check passed)", "text": text,
                                   "template": report["narrative"]["text"]}
        else:
            report["narrative"]["llm_note"] = err
            warnings.append(f"LLM narrative not used: {err}")
    rec = report.get("recommendation")
    if out["status"] != "ok":
        diag = out.get("diagnostics") or {}
        text = "No feasible strategy exists under the approved constraints. " + (diag.get("summary") or out.get("message", ""))
        status, kind, data = "infeasible", "infeasible", {"diagnostics": diag, "report": report}
    else:
        rows = [r for r in state["tradeoffs"]["table"] if not r.get("duplicate_of") and r.get("status") == "optimal"]
        text = (f"{out['message']} All figures come from the HiGHS optimizer and passed independent validation. "
                + (f"Recommended under your stated priorities: {rec['strategy_name']}. " if rec else "")
                + "The strategies are submitted for review; you can mark your preferred option, and an administrator "
                  "makes the final decision. No strategy is approved automatically.")
        status, kind, data = "strategies_ready", "results", {"tradeoffs": state["tradeoffs"], "report": report,
                                                             "n_compared": len(rows)}
    return {"decision_report": report, "status": status, "warnings": warnings, "messages": _msg(state, text, kind, data),
            "trace": _trace(state, "build_decision_report",
                            ("Recommendation: " + rec["strategy_name"]) if rec else "No recommendation (no feasible strategy)",
                            t0, ["recommend_strategy", "explain_results"])}


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
                     ("validate_formulation", validate_formulation),
                     ("validate_results", validate_results), ("analyze_tradeoffs", analyze_tradeoffs),
                     ("explain_strategies", explain_strategies), ("build_decision_report", build_decision_report_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["understand_request", "simulate_baseline"])
    g.add_edge("understand_request", "check_completeness")
    g.add_edge("check_completeness", "investigate_data")
    g.add_conditional_edges("investigate_data", _after_investigation, ["ask_clarification", "build_scenario"])
    g.add_conditional_edges("build_scenario", _after_build, ["ask_clarification", "formulate_problem"])
    g.add_edge("ask_clarification", END)
    g.add_edge("formulate_problem", "validate_formulation")
    g.add_edge("validate_formulation", END)
    g.add_edge("simulate_baseline", "optimize_strategies")
    g.add_edge("optimize_strategies", "validate_results")
    g.add_edge("validate_results", "analyze_tradeoffs")
    g.add_edge("analyze_tradeoffs", "explain_strategies")
    g.add_edge("explain_strategies", "build_decision_report")
    g.add_edge("build_decision_report", END)
    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


GRAPH_NODES = ["understand_request", "check_completeness", "investigate_data", "ask_clarification", "build_scenario",
               "formulate_problem", "validate_formulation", "simulate_baseline", "optimize_strategies", "validate_results",
               "analyze_tradeoffs", "explain_strategies", "build_decision_report"]


def run_workflow(state: PlanningState, llm: LLMClient | None = None) -> PlanningState:
    client = llm or LLMClient()
    cfg = {"configurable": {"llm": client}}
    before = len(getattr(client, "calls", []) or [])
    result = get_graph().invoke(state, config=cfg)
    new_calls = list((getattr(client, "calls", None) or [])[before:])
    result["llm_calls"] = list(state.get("llm_calls", [])) + new_calls
    return result
