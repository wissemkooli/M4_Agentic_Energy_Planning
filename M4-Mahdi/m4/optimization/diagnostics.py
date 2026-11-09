"""Infeasibility diagnosis with an elastic (goal-programming) reformulation.

When the original model is infeasible, every policy/reliability constraint receives a
non-negative slack variable and the weighted sum of slacks is minimized (weights in
`model.ELASTIC_WEIGHTS`). Non-zero slacks indicate which constraints cannot be met
simultaneously and by how much. This explains *why* no strategy exists; it never
produces a strategy that is presented as feasible.
"""
from __future__ import annotations

import pyomo.environ as pyo

from ..modeling.system import SystemInputs
from .model import build_model
from .solver import solve

MESSAGES = {
    "unserved_energy": ("Demand cannot be fully supplied", "GWh"),
    "renewable_target": ("Renewable share target not reachable", "GWh of renewable generation missing"),
    "gas_share": ("Gas share limit not reachable", "GWh of gas generation above the limit"),
    "emission_cap": ("CO2 cap not reachable", "MtCO2 above the cap"),
    "import_share": ("Import limit too strict", "GWh of imports above the limit"),
    "reserve_margin": ("Reserve margin not reachable", "MW of firm capacity missing"),
    "budget_total": ("Total investment budget insufficient", "MUSD missing"),
    "annual_budget": ("Annual investment budget insufficient", "MUSD missing (sum over years)"),
    "min_build": ("Mandated minimum build not reachable", "MW"),
}


def diagnose_infeasibility(inp: SystemInputs) -> dict:
    m = build_model(inp, elastic=True)
    m.objective = pyo.Objective(expr=m.elastic_penalty + 1e-6 * m.obj_expr["min_cost"], sense=pyo.minimize)
    outcome = solve(m)
    if not outcome.ok:
        return {
            "status": "hard_limits_conflict",
            "summary": ("Even with all policy constraints relaxed, the problem is infeasible: technical limits "
                        "(technology build limits, disabled investments, minimum builds or the variable "
                        "renewable integration limit) are contradictory."),
            "violations": [],
        }
    violations = []
    for name, var_name in m.slack_names:
        v = getattr(m, var_name)
        items = [(i, float(pyo.value(v[i]))) for i in v] if v.is_indexed() else [(None, float(pyo.value(v)))]
        nz = [(i, x) for i, x in items if x > 1e-4]
        if not nz:
            continue
        title, unit = MESSAGES[name]
        years = sorted({(i[0] if isinstance(i, tuple) else i) for i, _ in nz if i is not None})
        total = sum(x for _, x in nz)
        detail = {"constraint": name, "title": title, "total_shortfall": round(total, 3), "unit": unit,
                  "years": years}
        if name == "renewable_target":
            detail["percentage_points"] = {int(i): round(100 * x / inp.annual_supply(i), 2) for i, x in nz}
        violations.append(detail)
    if violations:
        parts = []
        for v in violations:
            yrs = f" (years {v['years'][0]}-{v['years'][-1]})" if v["years"] else ""
            parts.append(f"{v['title']}{yrs}: {v['total_shortfall']:,.1f} {v['unit']}")
        summary = "Minimum relaxation needed to restore feasibility — " + "; ".join(parts) + "."
    else:
        summary = "The elastic model found no violated constraint; infeasibility may be numerical."
    return {"status": "diagnosed", "summary": summary, "violations": violations,
            "method": "elastic reformulation (weighted slack minimization)"}
