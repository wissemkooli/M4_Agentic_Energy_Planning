"""Problem understanding, formulation checks, recommendation and the final decision report.

Everything here is deterministic and computed from the intent, the data and the validated
solver results. The optional LLM only rephrases the narrative; its text is accepted only if
every number it contains appears in the verified facts (same grounding check as explanations).
The recommendation is conditional on the priorities the user stated; the decision belongs
to the human.
"""
from __future__ import annotations

import json

from ..operations.schema import OP_OBJECTIVE_LABELS
from ..scenarios.schema import OBJECTIVE_LABELS
from .explain import ungrounded_numbers
from .llm import LLMClient, LLMError

# ------------------------------------------------------------------------------ understanding
SOURCE_USE = {
    "m1": "monthly electricity demand (forecast and uncertainty interval)",
    "m3": "international benchmark context (not used as cost or capacity inputs)",
    "reference": "existing installed and available capacity, 2024 generation",
    "assumption": "technology costs, emission factors, capacity factors, unit and system parameters",
}


def _use(source: str) -> str:
    s = source.lower()
    for k, v in SOURCE_USE.items():
        if k in s:
            return v
    return "input data"


def build_understanding(intent: dict, questions: list[dict]) -> dict:
    op = intent.get("problem_type") == "operational"
    if op:
        objectives = [OP_OBJECTIVE_LABELS[o] for o in intent.get("op_objectives", [])]
        period = intent.get("period_start") or intent.get("period_relative") or "not stated"
        horizon = f"{period}, {intent.get('period_months') or 1} month(s)"
        variables = ["number of gas units kept in service each month (integer)", "monthly generation by technology (GWh)",
                     "monthly imports (GWh)", "unserved energy (GWh, last resort)"]
        resources = ["existing fleet only (no investment)"]
        resources += [f"{o['technology']}: " + (f"{o['unavailable_mw']:g} MW unavailable" if o.get("unavailable_mw") is not None
                                                else f"capacity reduced by {o['reduction_pct']:g} %") for o in intent.get("capacity_outages", [])]
        if intent.get("gas_limit_gwh") is not None:
            resources.append(f"gas-fired generation limited to {intent['gas_limit_gwh']:g} GWh")
        constraints = ["monthly demand must be met (unserved energy is penalized)", "units in service limit gas output",
                       "operating reserve on firm capacity", "import and renewable integration limits"]
        if intent.get("demand_change_pct") is not None:
            constraints.insert(0, f"demand changed by {intent['demand_change_pct']:+g} % versus the M1 forecast")
        if intent.get("reserve_margin_pct") is not None:
            constraints.append(f"reserve margin {intent['reserve_margin_pct']:g} %")
        data = ["M1 monthly demand forecast", "2024 reference fleet", "technology and unit parameters"]
    else:
        objectives = [OBJECTIVE_LABELS[o] for o in intent.get("objectives", [])]
        horizon = f"{intent.get('start_year') or 2025}-{intent.get('end_year') or 2040}"
        variables = ["new capacity commissioned per technology and year (MW)", "monthly generation by technology (GWh)",
                     "monthly imports (GWh)"]
        resources = ["existing fleet (2024 reference minus retirements)"]
        if intent.get("budget_total_musd") is not None:
            resources.append(f"total investment budget {intent['budget_total_musd']:,.0f} MUSD")
        if intent.get("annual_budget_musd") is not None:
            resources.append(f"annual budget {intent['annual_budget_musd']:,.0f} MUSD")
        constraints = ["monthly energy balance", "reserve margin", "technology build limits"]
        for f, label in (("renewable_targets", "renewable share >= {v:g} % from {y}"),
                         ("emission_reduction_targets", "CO2 at least {v:g} % below 2024 from {y}"),
                         ("gas_share_limits", "gas share <= {v:g} % from {y}")):
            for t in intent.get(f, []):
                constraints.append(label.format(v=t["value"], y=t.get("year") or "the final year"))
        data = ["M1 demand forecasts", "2024 reference system", "technology assumptions", "M3 benchmarks (context)"]
    return {
        "problem_type": "operational" if op else "planning",
        "problem_type_label": ("Short-term operation of the existing fleet" if op else "Long-term capacity planning"),
        "objectives": objectives or ["not identified yet"], "decision_variables": variables, "resources": resources,
        "constraints": constraints, "time_horizon": horizon, "relevant_data": data,
        "missing_information": [q["question"] for q in questions if q.get("required")],
        "interpreted_by": "LLM (validated)" if intent.get("parser") == "llm" else "deterministic parser",
    }


# ------------------------------------------------------------------------------ recommendation
PLAN_METRIC = {
    "min_cost": ("total_cost_npv_musd", min, "the lowest discounted system cost", "MUSD"),
    "min_emissions": ("total_emissions_mt", min, "the lowest cumulative CO2 emissions", "MtCO2"),
    "max_renewables": ("renewable_share_final_year", max, "the highest final-year renewable share", "%"),
    "min_dependence": ("dependence_share_horizon", min, "the lowest dependence on gas and imports", "%"),
    "min_investment": ("total_investment_musd", min, "the lowest investment requirement", "MUSD"),
}
OP_METRIC = {
    "min_operating_cost": ("operating_cost_musd", min, "the lowest operating cost", "MUSD"),
    "min_unserved": ("unserved_gwh", min, "the least unserved energy", "GWh"),
    "min_gas": ("gas_gwh", min, "the lowest gas consumption", "GWh"),
    "min_emissions": ("emissions_mt", min, "the lowest CO2 emissions", "MtCO2"),
}


SHORT = {"min_operating_cost": "minimize operating cost", "min_unserved": "serve all demand (minimize unserved energy)",
         "min_gas": "minimize gas consumption", "min_emissions": "minimize CO2 emissions", "min_cost": "minimize total system cost",
         "max_renewables": "maximize renewable electricity", "min_dependence": "reduce dependence on gas and imports",
         "min_investment": "minimize investment"}


def _fmt(v: float, unit: str) -> str:
    return f"{v * 100:.1f} %" if unit == "%" else f"{v:,.1f} {unit}"


def recommend(problem_type: str, intent: dict, strategies: list[dict]) -> dict | None:
    cands = [s for s in strategies if s.get("feasible") and not s.get("duplicate_of") and s.get("report")]
    if not cands:
        return None
    op = problem_type == "operational"
    metrics = OP_METRIC if op else PLAN_METRIC
    objectives = (intent.get("op_objectives") if op else intent.get("objectives")) or (["min_operating_cost"] if op else ["min_cost"])
    objectives = [o for o in objectives if o in metrics] or (["min_operating_cost"] if op else ["min_cost"])
    h = {s["key"]: s["report"]["headline"] for s in cands}
    by_key = {s["key"]: s for s in cands}
    cost_key = "operating_cost_musd" if op else "total_cost_npv_musd"
    alternatives = []
    for o, (k, fn, label, unit) in metrics.items():
        best = fn(cands, key=lambda s: h[s["key"]][k])
        alternatives.append({"criterion": label, "strategy_key": best["key"], "strategy_name": best["name"],
                             "value": h[best["key"]][k], "unit": unit})

    balanced = "balanced_operations" if op else "balanced"
    cost_obj = "min_operating_cost" if op else "min_cost"
    env_objs = {"min_gas", "min_emissions"} if op else {"min_emissions", "max_renewables"}
    rec, reason = None, ""
    if cost_obj in objectives and env_objs & set(objectives) and balanced in by_key:
        lc = "least_cost_operations" if op else "cost_conscious"
        ext = "min_gas_use" if op else "max_decarbonization"
        env_k = "gas_gwh" if op else "total_emissions_mt"
        if lc in by_key and ext in by_key:
            c0, c1, cb = h[lc][cost_key], h[ext][cost_key], h[balanced][cost_key]
            e0, e1, eb = h[lc][env_k], h[ext][env_k], h[balanced][env_k]
            if abs(e0 - e1) > 1e-9 and abs(c1 - c0) > 1e-9:
                f_env, f_cost = (e0 - eb) / (e0 - e1), (cb - c0) / (c1 - c0)
                rec = by_key[balanced]
                reason = (f"You asked for both low cost and {'low gas use' if op else 'low emissions'}. "
                          f"'{rec['name']}' obtains {f_env * 100:.0f} % of the maximum achievable "
                          f"{'gas reduction' if op else 'emission reduction'} for {f_cost * 100:.0f} % of the extra cost of the "
                          f"most ambitious option ({_fmt(cb, 'MUSD')} vs {_fmt(c0, 'MUSD')} for the least-cost option).")
    if rec is None:
        primary = objectives[0]
        k, fn, label, unit = metrics[primary]
        best_v = fn(h[s["key"]][k] for s in cands)
        tied = [s for s in cands if abs(h[s["key"]][k] - best_v) <= max(0.005 * abs(best_v), 1e-6)]
        if len(tied) > 1:
            if op and primary == "min_unserved":
                tied.sort(key=lambda s: (-h[s["key"]]["min_reserve_margin"], h[s["key"]][cost_key]))
                tie_txt = "; among the options that serve the same demand it keeps the highest reserve margin"
            else:
                second = next((o for o in objectives[1:] if o in metrics), cost_obj)
                k2, fn2, label2, _ = metrics[second]
                tied.sort(key=lambda s: h[s["key"]][k2] if fn2 is min else -h[s["key"]][k2])
                tie_txt = f"; among the options within 0.5 % on this criterion it has {label2}"
        else:
            tie_txt = ""
        rec = tied[0]
        reason = (f"Your first priority is to {SHORT[primary]}. "
                  f"'{rec['name']}' has {label} ({_fmt(h[rec['key']][k], unit)}){tie_txt}.")
    trade = rec["report"].get("tradeoff_vs_least_cost")
    if trade:
        extra = trade.get("additional_operating_cost_musd" if op else "additional_cost_npv_musd")
        if extra is not None and extra > 0:
            reason += f" Compared with the least-cost option it costs {extra:,.2f} MUSD more."
    return {"strategy_key": rec["key"], "strategy_name": rec["name"], "reason": reason,
            "priority_basis": objectives, "alternatives": alternatives,
            "statement": ("This is a recommendation under the priorities you stated, computed from validated optimizer "
                          "results. If your priorities differ, another option may be preferable. The final decision is yours.")}


# ------------------------------------------------------------------------------ decision report
def build_decision_report(state: dict) -> dict:
    out = state["strategy_result"]
    intent = state.get("intent") or {}
    op = out.get("problem_type") == "operational"
    feasible = [s for s in out["strategies"] if s["feasible"]]
    distinct = [s for s in feasible if not s["duplicate_of"]]
    form = state.get("formulation") or {}
    first_run = (feasible[0]["run"] if feasible else out.get("base_run")) or {}
    data_used = [{"source": p.get("source"), "data_status": p.get("data_status"), "dataset_version": p.get("dataset_version"),
                  "path": p.get("path"), "used_for": _use(str(p.get("source")))} for p in first_run.get("provenance") or []]
    solves = sum(len((s["run"].get("solver") or {}).get("solves", [])) for s in out["strategies"])
    seconds = round(sum(x.get("seconds", 0) for s in out["strategies"] for x in (s["run"].get("solver") or {}).get("solves", [])), 3)
    rec = recommend("operational" if op else "planning", intent, out["strategies"]) if feasible else None
    opts = []
    for s in distinct:
        h = s["report"]["headline"]
        keys = (["operating_cost_musd", "unserved_gwh", "gas_gwh", "imports_gwh", "min_reserve_margin"] if op else
                ["total_cost_npv_musd", "total_investment_musd", "total_emissions_mt", "renewable_share_final_year",
                 "dependence_share_horizon"])
        opts.append({"key": s["key"], "name": s["name"], "objective": s["strategic_objective"],
                     "metrics": {k: h.get(k) for k in keys}, "advantages": s["report"]["advantages"],
                     "disadvantages": s["report"]["disadvantages"], "risks": s["report"]["risks"][:3]})
    limitations = [
        f"Input data status: {first_run.get('data_status', 'UNKNOWN')}. MOCK inputs illustrate the method; they are not "
        "official statistics." if first_run.get("data_status") != "REAL" else "Inputs declared REAL in the data manifest.",
        "The LLM only interprets the request and rephrases text; every number comes from Python tools and the HiGHS solver.",
        ("Monthly resolution: hourly peaks, ramping and start-ups are approximated." if op else
         "Monthly resolution, no storage, no network constraints, no construction lead times."),
    ] + list(out.get("notes") or [])
    report = {
        "problem_type": "operational" if op else "planning",
        "understanding": state.get("understanding"),
        "data_used": data_used,
        "investigation_tools": [c["tool"] for c in (state.get("investigation") or {}).get("tool_calls", [])],
        "assumptions": (state.get("assumptions_to_approve") or [])[:12],
        "formulation": {"problem_class": form.get("problem_class"), "objective": form.get("objective"),
                        "n_decision_variables": sum(v.get("count", 0) for v in form.get("decision_variables", [])),
                        "constraints": [c["name"] for c in form.get("constraints", [])],
                        "strategies": [{"name": p["name"], "method": p["method"]} for p in state.get("strategy_plan", [])]},
        "computation": {"solver": "HiGHS (Pyomo)", "solves": solves, "solver_seconds": seconds,
                        "n_strategies": len(out["strategies"]), "n_feasible": len(feasible), "n_distinct": len(distinct),
                        "all_validated": all(s["run"]["validation"]["status"] == "valid" for s in feasible) if feasible else False,
                        "status": out["status"]},
        "options": opts,
        "recommendation": rec,
        "limitations": limitations,
        "decision_owner": "human",
    }
    report["narrative"] = {"source": "template", "text": template_narrative(report, state)}
    return report


def template_narrative(r: dict, state: dict) -> str:
    u = r.get("understanding") or {}
    op = r["problem_type"] == "operational"
    p = []
    p.append(f"How the request was understood: {u.get('problem_type_label', r['problem_type'])} over {u.get('time_horizon')}. "
             f"Objective(s): {'; '.join(u.get('objectives', []))}. Decisions: {'; '.join(u.get('decision_variables', []))}.")
    p.append("Data used: " + "; ".join(f"{d['source']} ({d['data_status']}, {d['dataset_version']}) for {d['used_for']}"
                                        for d in r["data_used"]) + ".")
    f = r["formulation"]
    p.append(f"Mathematical formulation: {f.get('problem_class')} with {f.get('n_decision_variables'):,} decision variables "
             f"and the constraint groups {', '.join(f.get('constraints', []))}. Each alternative uses a different formulation: "
             + ", ".join(f"{s['name']} ({s['method']})" for s in f.get("strategies", [])) + ".")
    c = r["computation"]
    if c["status"] != "ok":
        p.append("The optimizer found no feasible solution under the approved constraints; the diagnosis lists the minimum "
                 "relaxation needed. No strategy is reported.")
        return "\n\n".join(p)
    p.append(f"What the optimizer calculated: HiGHS solved {c['solves']} optimization problems in {c['solver_seconds']} s. "
             f"{c['n_feasible']} strategies are feasible, {c['n_distinct']} have distinct decisions"
             + (", and all passed the independent validation." if c["all_validated"] else "; see validation issues."))
    for o in r["options"]:
        m = o["metrics"]
        if op:
            line = (f"{o['name']}: operating cost {m['operating_cost_musd']:,.1f} MUSD, unserved {m['unserved_gwh']:,.1f} GWh, "
                    f"gas {m['gas_gwh']:,.0f} GWh, imports {m['imports_gwh']:,.0f} GWh, minimum reserve "
                    f"{m['min_reserve_margin'] * 100:.1f} %.")
        else:
            line = (f"{o['name']}: system cost {m['total_cost_npv_musd']:,.0f} MUSD (NPV), investment "
                    f"{m['total_investment_musd']:,.0f} MUSD, cumulative CO2 {m['total_emissions_mt']:,.1f} Mt, renewables "
                    f"{m['renewable_share_final_year'] * 100:.1f} % in the final year.")
        if o["advantages"]:
            line += " Strength: " + o["advantages"][0]
        if o["disadvantages"]:
            line += " Weakness: " + o["disadvantages"][0]
        p.append(line)
    if r.get("recommendation"):
        p.append(f"Recommendation: {r['recommendation']['strategy_name']}. {r['recommendation']['reason']} "
                 f"{r['recommendation']['statement']}")
    p.append("Uncertainties and limitations: " + " ".join(r["limitations"][:3]))
    return "\n\n".join(p)


NARRATIVE_SYSTEM = """You explain the results of an electricity optimization study to a non-technical decision-maker.
Use ONLY the facts provided. Every number you write must appear in the facts. Do not add numbers, do not round
differently than 1 decimal, do not claim data is real if data_status is MOCK. Keep the recommendation conditional on the
user's priorities and state that the final decision belongs to the human. Write 6 short paragraphs: understanding, data,
formulation, results, recommendation, uncertainties. Return JSON: {"narrative": str}."""


def llm_narrative(report: dict, client: LLMClient) -> tuple[str | None, str | None]:
    facts = {k: report[k] for k in ("problem_type", "understanding", "data_used", "formulation", "computation", "options",
                                    "recommendation", "limitations")}
    try:
        data = client.json_completion(NARRATIVE_SYSTEM, json.dumps(facts, default=str), purpose="decision_narrative")
    except LLMError as exc:
        return None, str(exc)
    text = data.get("narrative")
    if isinstance(text, list):
        text = "\n\n".join(str(x) for x in text)
    if not isinstance(text, str) or len(text) < 80:
        return None, "LLM narrative rejected: missing or too short"
    bad = ungrounded_numbers(text, {**facts, "template": report["narrative"]["text"]})
    if bad:
        return None, f"LLM narrative rejected: numbers not found in verified results {bad[:5]}"
    return text, None
