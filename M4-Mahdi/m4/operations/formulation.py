"""Human-readable formulation of an operational problem (shown before approval)."""
from __future__ import annotations

from .inputs import OperationalInputs
from .schema import OP_OBJECTIVE_LABELS, OP_OBJECTIVE_UNITS, OpObjectiveConfig


def describe_op_formulation(inp: OperationalInputs, objective: OpObjectiveConfig | None = None) -> dict:
    obj = objective or inp.problem.objective
    if obj.method == "single":
        obj_text = OP_OBJECTIVE_LABELS[obj.primary]
    elif obj.method == "lexicographic":
        obj_text = "Priority order: " + " > ".join(obj.priorities) + f" (tolerance {obj.lexicographic_tolerance_pct:g} %)"
    else:
        obj_text = OP_OBJECTIVE_LABELS[obj.primary] + " subject to " + ", ".join(
            f"{k} <= {v:,.2f} {OP_OBJECTIVE_UNITS[k]}" for k, v in obj.epsilon_bounds.items())
    n = len(inp.periods)
    D = inp.dispatchable
    units_txt = "; ".join(f"{t}: {inp.units_available[(t, inp.periods[0])]} of {inp.units_total[(t, inp.periods[0])]} "
                          f"units x {inp.unit_size_mw:,.0f} MW available" for t in D)
    constraints = [
        {"name": "Monthly energy balance", "expression": "sum_t gen[t,p] + imp[p] + uns[p] = demand[p]",
         "values": f"{n} equation(s); demand from M1 '{inp.problem.demand_basis}'"
                   + (f" x (1 {inp.problem.demand_change_pct:+g} %)" if inp.problem.demand_change_pct else "")},
        {"name": "Gas output of units in service",
         "expression": "min_stable x units x size x avail x h/1000 <= gen[gas,p] <= units x size x avail x h/1000",
         "values": f"min_stable = {inp.min_stable * 100:.0f} %; {units_txt}"} if inp.unit_commitment else
        {"name": "Gas output", "expression": "gen[gas,p] <= units x size x avail x h/1000 (units continuous)", "values": units_txt},
        {"name": "Renewable and hydro potential", "expression": "gen[t,p] <= capacity x capacity factor[t,month] x h/1000",
         "values": "monthly net capacity factors (data/input/assumptions)"},
        {"name": "Variable renewable integration", "expression": "gen[solar,p] + gen[wind,p] <= share_max x demand[p]",
         "values": f"share_max = {inp.max_vre_share * 100:.0f} %"},
        {"name": "Operating reserve", "expression": "firm capacity in service >= (1 + r) x peak[p]",
         "values": f"r = {inp.reserve_margin * 100:.1f} %; peak from M1 '{inp.problem.reserve_demand_basis or inp.problem.demand_basis}'"},
    ]
    if inp.import_enabled:
        constraints.append({"name": "Import limit", "expression": "imp[p] <= share x demand[p]",
                            "values": f"{inp.max_import_share * 100:.1f} %"})
    if inp.gas_limit_gwh is not None:
        constraints.append({"name": "Gas (fuel) availability", "expression": "sum_p gen[gas,p] <= G",
                            "values": f"G = {inp.gas_limit_gwh:,.0f} GWh"})
    constraints.append({"name": "Unserved energy",
                        "expression": "uns[p] >= 0, penalized at the value of lost load" if inp.allow_unserved else "uns[p] = 0",
                        "values": f"VOLL = {inp.voll:,.0f} USD/MWh" if inp.allow_unserved else "demand must be fully met"})
    return {
        "problem_type": "operational",
        "problem_class": ("Mixed-integer linear program (MILP: integer gas units in service), solved with HiGHS through Pyomo"
                          if inp.unit_commitment else "Linear program (LP relaxation), solved with HiGHS through Pyomo"),
        "horizon": f"{inp.labels[inp.periods[0]]} to {inp.labels[inp.periods[-1]]} ({n} month(s))",
        "method": obj.method,
        "method_explanation": {"single": "One objective optimized subject to all constraints.",
                               "lexicographic": "Objectives optimized in strict priority order; each optimum is kept within a tolerance.",
                               "epsilon_constraint": "Primary objective optimized while the other objectives are bounded."}[obj.method],
        "objective": obj_text,
        "decision_variables": [
            {"symbol": "units[gas,p]", "unit": "units", "meaning": "gas units kept in service (integer)" if inp.unit_commitment
             else "gas units in service (continuous)", "count": len(D) * n},
            {"symbol": "gen[t,p]", "unit": "GWh", "meaning": "monthly generation by technology", "count": len(inp.techs) * n},
            {"symbol": "imp[p]", "unit": "GWh", "meaning": "monthly imports", "count": n},
            {"symbol": "uns[p]", "unit": "GWh", "meaning": "unserved energy (last resort)", "count": n},
        ],
        "constraints": constraints,
        "assumptions": inp.assumptions,
        "data_status": inp.data_status,
    }
