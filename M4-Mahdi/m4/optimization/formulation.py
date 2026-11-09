"""Human-readable mathematical formulation of a scenario (shown before approval)."""
from __future__ import annotations

from ..modeling.system import SystemInputs
from ..scenarios.schema import OBJECTIVE_LABELS, OBJECTIVE_UNITS

METHOD_EXPLANATIONS = {
    "single": "Single-objective LP: one objective is optimized subject to all constraints.",
    "weighted_sum": ("Weighted sum of normalized objectives. Each objective is scaled to [0, 1] using its "
                     "utopia (best) and nadir (worst) values from a payoff table, then weighted. Finds supported "
                     "Pareto-optimal points; results depend on the weights."),
    "epsilon_constraint": ("Epsilon-constraint: the primary objective is optimized while every other selected "
                           "objective is bounded by a user-defined level. Can reach any Pareto-optimal point and the "
                           "bounds have a direct physical meaning."),
    "lexicographic": ("Lexicographic: objectives are optimized in strict priority order; after each stage the "
                      "achieved value is kept within a tolerance while the next objective is optimized."),
}


def describe_formulation(inp: SystemInputs) -> dict:
    sc = inp.scenario
    obj = sc.objective
    if obj.method == "single":
        obj_text = f"{OBJECTIVE_LABELS[obj.primary]}"
    elif obj.method == "weighted_sum":
        obj_text = "Minimize " + " + ".join(f"{w:g} x normalized[{k}]" for k, w in obj.weights.items())
    elif obj.method == "epsilon_constraint":
        b = ", ".join(f"{k} <= {('level ' + format(v.value, 'g') + ' ' + OBJECTIVE_UNITS[k]) if v.mode == 'absolute' else format(v.value * 100, '.0f') + '% of the way from best to worst'}"
                      for k, v in obj.epsilon_bounds.items())
        obj_text = f"{OBJECTIVE_LABELS[obj.primary]} subject to {b}"
    else:
        obj_text = "Priority order: " + " > ".join(obj.priorities) + f" (tolerance {obj.lexicographic_tolerance_pct:g} %)"

    constraints = [
        {"name": "Monthly energy balance", "expression": "sum_t gen[t,y,m] + imp[y,m] = demand[y,m] x (1 + losses)",
         "values": f"{len(inp.years) * 12} monthly equations"},
        {"name": "Available capacity", "expression": "gen[t,y,m] <= cap[t,y] x factor[t,m] x hours[y,m] / 1000",
         "values": "factor = availability (gas) or net monthly capacity factor (solar, wind, hydro)"},
        {"name": "Capacity evolution", "expression": "cap[t,y] = existing[t,y] + sum of new[t,y'] still in service",
         "values": "existing capacity follows the 2024 reference minus scheduled retirements"},
        {"name": "Reserve margin", "expression": "sum_t credit_t x cap[t,y] >= (1 + margin) x peak[y]",
         "values": f"margin = {inp.reserve_margin * 100:.1f} %"},
        {"name": "Variable renewable integration", "expression": "sum_{solar,wind} gen <= share_max x demand (monthly)",
         "values": f"share_max = {inp.max_vre_share * 100:.0f} %"},
    ]
    if inp.import_enabled:
        constraints.append({"name": "Import limit", "expression": "sum_m imp[y,m] <= share x annual supply",
                            "values": f"{inp.max_import_share * 100:.1f} %"})
    else:
        constraints.append({"name": "Imports disabled", "expression": "imp[y,m] = 0", "values": ""})
    if inp.budget_total is not None:
        constraints.append({"name": "Total investment budget", "expression": "sum capex_t x new[t,y] <= B",
                            "values": f"B = {inp.budget_total:,.0f} MUSD"})
    if inp.annual_budget is not None:
        constraints.append({"name": "Annual investment budget", "expression": "sum_t capex_t x new[t,y] <= B_y",
                            "values": f"B_y = {inp.annual_budget:,.0f} MUSD"})
    if inp.re_min_share:
        constraints.append({"name": "Renewable share target", "expression": "renewable gen[y] >= share_y x supply[y]",
                            "values": _targets(sc.constraints.renewable_share_targets, "%")})
    if inp.emission_cap_mt:
        vals = []
        if sc.constraints.emission_reduction_targets:
            vals.append(_targets(sc.constraints.emission_reduction_targets, "% below 2024 "
                                 f"({inp.baseline_emissions_mt:.2f} Mt)"))
        if sc.constraints.emission_caps_mt:
            vals.append(_targets(sc.constraints.emission_caps_mt, " Mt"))
        constraints.append({"name": "CO2 emission cap", "expression": "E[y] <= cap_y", "values": "; ".join(vals)})
    if inp.gas_max_share:
        constraints.append({"name": "Gas share limit", "expression": "gas gen[y] <= share_y x supply[y]",
                            "values": _targets(sc.constraints.max_gas_share, "%")})
    limits = [f"{t}: <= {x.max_annual_build:,.0f} MW/yr, <= {x.max_total_new:,.0f} MW total"
              + ("" if x.allow_investment else " (investment disabled)")
              + (f", >= {x.min_total_new:,.0f} MW total" if x.min_total_new else "")
              for t, x in inp.techs.items()]
    constraints.append({"name": "Technology build limits", "expression": "0 <= new[t,y] <= max_annual_t; sum_y new <= max_total_t",
                        "values": "; ".join(limits)})
    return {
        "problem_class": "Linear program (LP), solved with HiGHS through Pyomo",
        "horizon": f"{sc.start_year}-{sc.end_year} ({len(inp.years)} years x 12 months)",
        "method": obj.method,
        "method_explanation": METHOD_EXPLANATIONS[obj.method],
        "objective": obj_text,
        "decision_variables": [
            {"symbol": "new[t,y]", "unit": "MW", "meaning": "capacity commissioned at the start of year y", "count": len(inp.techs) * len(inp.years)},
            {"symbol": "gen[t,y,m]", "unit": "GWh", "meaning": "monthly generation used", "count": len(inp.techs) * len(inp.years) * 12},
            {"symbol": "imp[y,m]", "unit": "GWh", "meaning": "monthly imports", "count": len(inp.years) * 12},
        ],
        "constraints": constraints,
        "assumptions": inp.assumptions,
        "data_status": inp.data_status,
    }


def _targets(items, unit: str) -> str:
    return ", ".join(f"{i.value:g}{unit} from {i.year}" for i in items)
