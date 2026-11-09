"""Scenario schema validation, predefined scenarios and strategy generation."""
import pytest
from pydantic import ValidationError

from m4.modeling.system import build_system_inputs
from m4.scenarios.predefined import predefined_scenarios
from m4.scenarios.schema import ConstraintSet, ObjectiveConfig, ScenarioConfig, YearValue
from m4.strategy.generator import generate_strategies


def test_invalid_horizon():
    with pytest.raises(ValidationError, match="end_year"):
        ScenarioConfig(name="bad", start_year=2030, end_year=2026)
    with pytest.raises(ValidationError):
        ScenarioConfig(name="bad", start_year=2024)


def test_target_outside_horizon():
    with pytest.raises(ValidationError, match="outside the horizon"):
        ScenarioConfig(name="bad", end_year=2030, constraints=ConstraintSet(
            renewable_share_targets=[YearValue(year=2035, value=40)]))


def test_percentage_bounds_and_units():
    with pytest.raises(ValidationError, match="0-100"):
        ConstraintSet(renewable_share_targets=[YearValue(year=2030, value=140)])
    with pytest.raises(ValidationError):
        ConstraintSet(budget_total_musd=-5)
    with pytest.raises(ValidationError, match="annual budget"):
        ScenarioConfig(name="bb test", constraints=ConstraintSet(budget_total_musd=100, annual_budget_musd=200))


def test_objective_method_validation():
    with pytest.raises(ValidationError, match="weighted_sum"):
        ObjectiveConfig(method="weighted_sum", weights={"min_cost": 1})
    with pytest.raises(ValidationError, match="lexicographic"):
        ObjectiveConfig(method="lexicographic", priorities=["min_cost"])
    with pytest.raises(ValidationError, match="epsilon"):
        ObjectiveConfig(method="epsilon_constraint")


def test_predefined_scenarios_differ_in_parameters():
    sc = predefined_scenarios()
    assert len(sc) == 5
    dumps = [s.model_dump(exclude={"name", "description"}) for s in sc.values()]
    assert len({str(d) for d in dumps}) == 5
    assert sc["stress"].demand.basis == "upper_80"
    assert sc["energy_efficiency"].demand.efficiency_savings_pct_by_end > 0
    assert sc["accelerated_renewables"].constraints.renewable_share_targets


@pytest.mark.parametrize("key", ["reference", "stress"])
def test_predefined_scenarios_solve(bundle, key):
    from m4.optimization.engine import run_optimization
    r = run_optimization(build_system_inputs(bundle, predefined_scenarios()[key]))
    assert r["status"] == "optimal" and r["validation"]["status"] == "valid"


def test_strategy_generation_distinct_and_reported(bundle):
    sc = ScenarioConfig(name="ss test", end_year=2032, constraints=ConstraintSet(budget_total_musd=6000))
    out = generate_strategies(bundle, sc, ["min_emissions", "min_cost"])
    assert out["status"] == "ok"
    keys = [s["key"] for s in out["strategies"]]
    assert keys == ["cost_conscious", "max_decarbonization", "balanced"]
    cc, md = out["strategies"][0], out["strategies"][1]
    assert md["run"]["objective_values"]["min_emissions"] <= cc["run"]["objective_values"]["min_emissions"]
    for s in out["strategies"]:
        assert s["feasible"] and s["report"]["roadmap"] and s["report"]["risks"]
        assert s["run"]["validation"]["status"] == "valid"
    assert md["report"]["tradeoff_vs_least_cost"]["emissions_avoided_mt"] >= 0


def test_strategy_generation_infeasible(bundle):
    sc = ScenarioConfig(name="ss test", end_year=2028, constraints=ConstraintSet(
        renewable_share_targets=[YearValue(year=2026, value=80)]))
    out = generate_strategies(bundle, sc)
    assert out["status"] == "infeasible" and out["strategies"] == []
    assert out["diagnostics"]["violations"]
