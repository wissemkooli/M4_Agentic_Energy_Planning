"""Pyomo/HiGHS optimization: feasibility, constraints, multi-objective methods, failures."""
import pytest

from m4.modeling.system import build_system_inputs
from m4.optimization import engine
from m4.optimization.engine import pareto_frontier, run_optimization
from m4.scenarios.schema import (
    ConstraintSet, EpsilonBound, ObjectiveConfig, OperatingAssumptions, ScenarioConfig, TechnologyLimit, YearValue,
)


def _run(bundle, objective=None, **kw):
    sc = ScenarioConfig(name="opt test", start_year=2025, end_year=kw.pop("end_year", 2030), **kw)
    return run_optimization(build_system_inputs(bundle, sc), objective)


def test_small_case_optimal_and_validated(bundle):
    r = _run(bundle)
    assert r["status"] == "optimal" and r["feasible"]
    v = r["validation"]
    assert v["status"] == "valid"
    assert all(c["passed"] for c in v["physical_checks"])
    assert all(o["consistent"] for o in v["objective_consistency"].values())
    assert v["simulation_check"]["adequate"]
    assert r["results"]["summary"]["total_unserved_gwh"] == 0
    assert r["provenance"] and r["data_status"] == "MOCK"


def test_reserve_margin_respected(bundle):
    r = _run(bundle, constraints=ConstraintSet(reserve_margin_pct=20))
    for a in r["results"]["annual"]:
        assert a["reserve_margin_achieved"] >= 0.20 - 1e-6


def test_renewable_target_binding(bundle):
    free = _run(bundle)["results"]["annual"][2]["renewable_share"]  # 2027, least cost
    best = _run(bundle, ObjectiveConfig(primary="max_renewables"))["results"]["annual"][2]["renewable_share"]
    assert best > free
    target = round(100 * (free + best) / 2, 3)
    r = _run(bundle, constraints=ConstraintSet(renewable_share_targets=[YearValue(year=2027, value=target)]))
    a = {x["year"]: x for x in r["results"]["annual"]}
    assert all(a[y]["renewable_share"] >= target / 100 - 1e-4 for y in range(2027, 2031))
    assert a[2027]["renewable_share"] > free + 1e-4  # the target actually changes the solution


def test_budget_respected_and_binding(bundle):
    free = _run(bundle)["results"]["summary"]["total_investment_musd"]
    r = _run(bundle, constraints=ConstraintSet(budget_total_musd=free * 0.8))
    assert r["feasible"]
    assert r["results"]["summary"]["total_investment_musd"] <= free * 0.8 + 1e-3


def test_emission_cap(bundle):
    r = _run(bundle, constraints=ConstraintSet(emission_reduction_targets=[YearValue(year=2030, value=20)]))
    a = r["results"]["annual"][-1]
    assert a["emissions_mt"] <= r["baseline_2024"]["emissions_mt"] * 0.8 + 1e-4


def test_impossible_renewable_target_is_infeasible_with_diagnosis(bundle):
    r = _run(bundle, constraints=ConstraintSet(renewable_share_targets=[YearValue(year=2026, value=70)]))
    assert r["status"] == "infeasible" and not r["feasible"]
    assert r["results"] is None and r["objective_values"] is None  # nothing fabricated
    names = {v["constraint"] for v in r["diagnostics"]["violations"]}
    assert "renewable_target" in names


def test_insufficient_budget_is_infeasible(bundle):
    r = _run(bundle, constraints=ConstraintSet(budget_total_musd=50))
    assert r["status"] == "infeasible"
    assert {v["constraint"] for v in r["diagnostics"]["violations"]} & {"budget_total", "reserve_margin", "unserved_energy"}


def test_conflicting_hard_limits(bundle):
    sc = ConstraintSet(technology_limits=[TechnologyLimit(technology=t, allow_investment=False)
                                          for t in ("gas", "solar", "wind", "hydro")])
    r = _run(bundle, constraints=sc, operating=OperatingAssumptions(allow_imports=False))
    assert r["status"] == "infeasible"
    assert r["diagnostics"]["status"] in ("diagnosed", "hard_limits_conflict")


def test_min_emissions_lower_than_min_cost(bundle):
    c = _run(bundle)["objective_values"]
    e = _run(bundle, ObjectiveConfig(primary="min_emissions"))["objective_values"]
    assert e["min_emissions"] <= c["min_emissions"] + 1e-6
    assert e["min_cost"] >= c["min_cost"] - 1e-6


def test_weighted_sum_between_extremes(bundle):
    r = _run(bundle, ObjectiveConfig(method="weighted_sum", weights={"min_cost": 0.5, "min_emissions": 0.5}))
    p = r["payoff_table"]
    assert r["status"] == "optimal"
    for k in ("min_cost", "min_emissions"):
        assert p["utopia"][k] - 1e-4 <= r["objective_values"][k] <= p["nadir"][k] + 1e-4


def test_epsilon_constraint_bound(bundle):
    r = _run(bundle, ObjectiveConfig(method="epsilon_constraint", primary="min_cost",
                                     epsilon_bounds={"min_emissions": EpsilonBound(mode="fraction", value=0.5)}))
    p = r["payoff_table"]
    eps = p["utopia"]["min_emissions"] + 0.5 * (p["nadir"]["min_emissions"] - p["utopia"]["min_emissions"])
    assert r["objective_values"]["min_emissions"] <= eps + 1e-3


def test_lexicographic_keeps_first_priority(bundle):
    best = _run(bundle, ObjectiveConfig(primary="min_emissions"))["objective_values"]["min_emissions"]
    r = _run(bundle, ObjectiveConfig(method="lexicographic", priorities=["min_emissions", "min_cost"],
                                     lexicographic_tolerance_pct=1.0))
    assert r["objective_values"]["min_emissions"] <= best * 1.01 + 1e-4


def test_pareto_frontier_monotone(bundle):
    inp = build_system_inputs(bundle, ScenarioConfig(name="pp test", end_year=2028))
    pf = pareto_frontier(inp, "min_cost", "min_emissions", 4)
    pts = [p for p in pf["points"] if p["status"] == "optimal"]
    assert len(pts) == 4
    costs = [p["objective_values"]["min_cost"] for p in pts]
    ems = [p["objective_values"]["min_emissions"] for p in pts]
    assert all(costs[i] <= costs[i + 1] + 1e-3 for i in range(3))   # tighter emission caps cost more
    assert all(ems[i] >= ems[i + 1] - 1e-3 for i in range(3))


def test_solver_failure_returns_no_results(bundle, monkeypatch):
    from m4.optimization import solver as solver_mod

    def broken(_name=None):
        raise solver_mod.SolverUnavailableError("HiGHS missing")
    monkeypatch.setattr(solver_mod, "get_solver", broken)
    r = _run(bundle)
    assert r["status"] == "error" and r["results"] is None and "HiGHS missing" in r["message"]


def test_solver_exception_handled(bundle, monkeypatch):
    from m4.optimization import solver as solver_mod

    class Boom:
        config = type("C", (), {})()

        def solve(self, *a, **k):
            raise RuntimeError("crash")
    monkeypatch.setattr(solver_mod, "get_solver", lambda _n=None: ("appsi_highs", Boom()))
    r = _run(bundle)
    assert r["status"] == "error" and not r["feasible"]
