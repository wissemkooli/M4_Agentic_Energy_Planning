"""Independent validation of optimization results.

Checks are recomputed from the extracted solution (not trusted from the solver):
1. physical consistency: energy balance, output limits, non-negativity, build limits;
2. objective consistency: Pyomo objective expressions vs independently recomputed indicators;
3. constraint satisfaction report for every policy constraint (value, limit, margin);
4. adequacy re-check with the merit-order simulation using the optimized capacity plan.
"""
from __future__ import annotations

import pyomo.environ as pyo

from ..modeling.indicators import capacity_by_year
from ..modeling.simulation import simulate
from ..modeling.system import SystemInputs

TOL = 1e-4


def physical_checks(inp: SystemInputs, new, gen, imp) -> list[dict]:
    cap = capacity_by_year(inp, new)
    checks = []
    worst_bal = max(abs(sum(gen[(t, y, m)] for t in inp.techs) + imp[(y, m)] - inp.supply_req_gwh[(y, m)])
                    for y in inp.years for m in inp.months)
    checks.append({"check": "Monthly energy balance", "max_residual_gwh": round(worst_bal, 6),
                   "passed": worst_bal <= 1e-3})
    worst_out = max(gen[(t, y, m)] - inp.potential_gwh(t, y, m, cap[(t, y)])
                    for t in inp.techs for y in inp.years for m in inp.months)
    checks.append({"check": "Generation within available capacity", "max_excess_gwh": round(max(0, worst_out), 6),
                   "passed": worst_out <= 1e-3})
    neg = min(min(gen.values()), min(imp.values()), min(new.values()))
    checks.append({"check": "Non-negativity", "min_value": round(neg, 6), "passed": neg >= -1e-6})
    over_build = [t for t, x in inp.techs.items()
                  if sum(new[(t, y)] for y in inp.years) > x.max_total_new + 1e-3
                  or any(new[(t, y)] > x.max_annual_build + 1e-3 for y in inp.years)]
    checks.append({"check": "Technology build limits", "violations": over_build, "passed": not over_build})
    return checks


def constraint_report(inp: SystemInputs, results: dict) -> list[dict]:
    rows = []
    annual = {a["year"]: a for a in results["annual"]}

    def add(name, scope, value, limit, kind, unit):
        ok = value >= limit - TOL * max(1, abs(limit)) if kind == ">=" else value <= limit + TOL * max(1, abs(limit))
        rows.append({"constraint": name, "scope": scope, "value": round(value, 4), "limit": round(limit, 4),
                     "relation": kind, "unit": unit, "satisfied": bool(ok),
                     "margin": round((value - limit) if kind == ">=" else (limit - value), 4)})

    if inp.budget_total is not None:
        add("Total investment budget", "horizon", results["summary"]["total_investment_musd"], inp.budget_total,
            "<=", "MUSD")
    if inp.annual_budget is not None:
        y_max = max(annual.values(), key=lambda a: a["investment_musd"])
        add("Annual investment budget", f"max year {y_max['year']}", y_max["investment_musd"], inp.annual_budget,
            "<=", "MUSD")
    for y, s in inp.re_min_share.items():
        add("Minimum renewable share", str(y), annual[y]["renewable_share"] * 100, s * 100, ">=", "%")
    for y, s in inp.gas_max_share.items():
        add("Maximum gas share", str(y), annual[y]["gas_share"] * 100, s * 100, "<=", "%")
    for y, c in inp.emission_cap_mt.items():
        add("Annual CO2 cap", str(y), annual[y]["emissions_mt"], c, "<=", "MtCO2")
    worst_rm = min(annual.values(), key=lambda a: a["reserve_margin_achieved"])
    add("Reserve margin (worst year)", str(worst_rm["year"]), worst_rm["reserve_margin_achieved"] * 100,
        inp.reserve_margin * 100, ">=", "%")
    if inp.import_enabled:
        worst_imp = max(annual.values(), key=lambda a: a["import_share"])
        add("Maximum import share (worst year)", str(worst_imp["year"]), worst_imp["import_share"] * 100,
            inp.max_import_share * 100, "<=", "%")
    add("Unserved energy", "horizon", results["summary"]["total_unserved_gwh"], 0.0, "<=", "GWh")
    for t, x in inp.techs.items():
        if x.min_total_new > 0:
            add(f"Minimum new {t} capacity", "horizon", results["summary"]["total_new_capacity_mw"][t],
                x.min_total_new, ">=", "MW")
    return rows


def objective_consistency(model: pyo.ConcreteModel, results: dict) -> dict:
    out = {}
    for k, expr in model.obj_expr.items():
        solver_val = float(pyo.value(expr))
        recomputed = float(results["objective_values"][k])
        rel = abs(solver_val - recomputed) / max(1.0, abs(recomputed))
        out[k] = {"solver_expression": round(solver_val, 4), "recomputed": round(recomputed, 4),
                  "relative_difference": rel, "consistent": rel < 1e-3}
    return out


def validate_solution(inp: SystemInputs, model, new, gen, imp, results: dict) -> dict:
    phys = physical_checks(inp, new, gen, imp)
    cons = constraint_report(inp, results)
    obj = objective_consistency(model, results)
    sim = simulate(inp, new)
    adequacy = {
        "method": "merit-order re-simulation of the optimized capacity plan",
        "unserved_gwh": sim["summary"]["total_unserved_gwh"],
        "adequate": sim["summary"]["total_unserved_gwh"] <= 1e-2,
        "issues": [i for i in sim["adequacy_issues"] if "cannot be supplied" in i],
    }
    passed = all(c["passed"] for c in phys) and all(c["satisfied"] for c in cons) \
        and all(o["consistent"] for o in obj.values()) and adequacy["adequate"]
    return {"status": "valid" if passed else "invalid", "physical_checks": phys, "constraints": cons,
            "objective_consistency": obj, "simulation_check": adequacy}
