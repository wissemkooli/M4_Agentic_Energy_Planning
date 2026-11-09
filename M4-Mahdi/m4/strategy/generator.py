"""Alternative strategy generation.

For one planning situation (a base scenario holding the user's constraints), several
strategies are produced by DIFFERENT optimization formulations — never by rewording:

  cost_conscious      single objective: minimize system cost (NPV)
  max_decarbonization lexicographic: minimize CO2, then cost (within tolerance)
  balanced            epsilon-constraint: minimize cost with CO2 bounded at the midpoint
                      between the two strategies above
  energy_security     lexicographic: minimize external dependence, then cost
  max_renewables      lexicographic: maximize renewable generation, then cost

The set of strategies follows the objectives requested by the user. After solving, the
generator compares investment plans AND annual dispatch; a strategy numerically identical
to another one (within 1 MW / 1 GWh) is flagged as a duplicate instead of being presented as a distinct option.
"""
from __future__ import annotations

from ..modeling.system import SystemInputs, build_system_inputs
from ..optimization.engine import run_optimization
from ..scenarios.schema import EpsilonBound, ObjectiveConfig, ScenarioConfig
from .roadmap import build_strategy_report

STRATEGY_TEMPLATES = {
    "cost_conscious": {
        "name": "Cost-conscious transition",
        "objective": "Minimize total discounted system cost while meeting every constraint",
        "config": lambda **_: ObjectiveConfig(method="single", primary="min_cost"),
    },
    "max_decarbonization": {
        "name": "Maximum decarbonization",
        "objective": "Minimize cumulative CO2 emissions first, then cost (1 % tolerance)",
        "config": lambda **_: ObjectiveConfig(method="lexicographic", priorities=["min_emissions", "min_cost"],
                                              lexicographic_tolerance_pct=1.0),
    },
    "balanced": {
        "name": "Balanced transition",
        "objective": "Minimize cost with cumulative CO2 bounded halfway between the cost-optimal and minimum-emission plans",
        "config": lambda eps=None, **_: ObjectiveConfig(
            method="epsilon_constraint", primary="min_cost",
            epsilon_bounds={"min_emissions": EpsilonBound(mode="absolute", value=eps)}),
    },
    "energy_security": {
        "name": "Energy security",
        "objective": "Minimize externally dependent supply (gas and imports) first, then cost (1 % tolerance)",
        "config": lambda **_: ObjectiveConfig(method="lexicographic", priorities=["min_dependence", "min_cost"],
                                              lexicographic_tolerance_pct=1.0),
    },
    "max_renewables": {
        "name": "Renewable leadership",
        "objective": "Maximize renewable generation first, then cost (1 % tolerance)",
        "config": lambda **_: ObjectiveConfig(method="lexicographic", priorities=["max_renewables", "min_cost"],
                                              lexicographic_tolerance_pct=1.0),
    },
}


def select_strategy_keys(objectives: list[str]) -> list[str]:
    keys = ["cost_conscious", "max_decarbonization", "balanced"]
    if "min_dependence" in objectives:
        keys.append("energy_security")
    if "max_renewables" in objectives:
        keys.append("max_renewables")
    return keys


def _plan_signature(result: dict) -> list[float]:
    """Investment decisions (MW) and annual dispatch (GWh): two strategies are duplicates only if both match."""
    sig = []
    for a in result["results"]["annual"]:
        sig += list(a["new_capacity_mw"].values()) + list(a["generation_gwh"].values()) + [a["imports_gwh"]]
    return sig


def _same_plan(a: dict, b: dict, tol: float = 1.0) -> bool:
    sa, sb = _plan_signature(a), _plan_signature(b)
    return len(sa) == len(sb) and max(abs(x - y) for x, y in zip(sa, sb)) <= tol


def generate_strategies(bundle, base: ScenarioConfig, objectives: list[str] | None = None) -> dict:
    objectives = objectives or ["min_cost", "min_emissions"]
    keys = select_strategy_keys(objectives)
    inp: SystemInputs = build_system_inputs(bundle, base)
    runs: dict[str, dict] = {}

    for key in [k for k in keys if k != "balanced"]:
        runs[key] = run_optimization(inp, STRATEGY_TEMPLATES[key]["config"](), diagnose=(key == "cost_conscious"))
        if key == "cost_conscious" and not runs[key]["feasible"]:
            # same constraints for every alternative: if the base problem is infeasible, all are
            return {"status": "infeasible", "strategies": [], "diagnostics": runs[key].get("diagnostics"),
                    "message": runs[key].get("message"), "base_run": runs[key]}

    if "balanced" in keys:
        if runs.get("cost_conscious", {}).get("feasible") and runs.get("max_decarbonization", {}).get("feasible"):
            e_cost = runs["cost_conscious"]["objective_values"]["min_emissions"]
            e_min = runs["max_decarbonization"]["objective_values"]["min_emissions"]
            eps = e_min + 0.5 * (e_cost - e_min)
            runs["balanced"] = run_optimization(inp, STRATEGY_TEMPLATES["balanced"]["config"](eps=eps))
        else:
            runs["balanced"] = {"status": "untested", "feasible": False,
                                "message": "Balanced strategy requires both cost-optimal and minimum-emission runs."}

    strategies = []
    for key in keys:
        run = runs[key]
        tpl = STRATEGY_TEMPLATES[key]
        duplicate_of = None
        if run.get("feasible"):
            for prev in strategies:
                if prev["feasible"] and _same_plan(run, runs[prev["key"]]):
                    duplicate_of = prev["key"]
                    break
        strategies.append({"key": key, "name": tpl["name"], "strategic_objective": tpl["objective"],
                           "feasible": bool(run.get("feasible")), "status": run.get("status"),
                           "duplicate_of": duplicate_of, "run": run})

    feasible = [s for s in strategies if s["feasible"]]
    for s in strategies:
        s["report"] = build_strategy_report(s, feasible, inp) if s["feasible"] else None
    distinct = [s for s in feasible if not s["duplicate_of"]]
    return {"status": "ok" if feasible else "infeasible", "strategies": strategies,
            "n_feasible": len(feasible), "n_distinct": len(distinct),
            "message": f"{len(feasible)} feasible strategies, {len(distinct)} with distinct investment plans."}
