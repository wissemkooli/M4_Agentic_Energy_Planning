"""Demand pathway, system inputs and merit-order simulation."""
import pytest

from m4.data_integration.adapters import M1InputAdapter, SourceSpec
from m4.data_integration.provenance import DataStatus
from m4.data_integration.repository import InputBundle
from m4.modeling.demand import DemandPathwayError, build_demand_pathway
from m4.modeling.simulation import simulate
from m4.modeling.system import ScenarioDataError, build_system_inputs
from m4.scenarios.schema import ConstraintSet, DemandAssumptions, ScenarioConfig, TechnologyLimit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_demand_uses_m1_forecast(bundle):
    df, meta = build_demand_pathway(bundle.demand, ScenarioConfig(name="dd test"))
    assert len(df) == 16 * 12 and set(df["source"]) == {"M1_forecast"}
    assert df[df["year"] == 2025]["demand_gwh"].sum() == pytest.approx(20180, rel=1e-9)
    assert meta["extrapolated_years"] == []


def test_demand_extrapolation_is_labelled(bundle):
    legacy = M1InputAdapter(SourceSpec("m1", ROOT / "data/mock/legacy/m1_forecast.csv", DataStatus.MOCK, "v"), ROOT).load()
    sc = ScenarioConfig(name="dd test", end_year=2027, demand=DemandAssumptions(extrapolation_growth_pct=3.0))
    df, meta = build_demand_pathway(legacy, sc)
    assert meta["extrapolated_years"] == [2026, 2027]
    jan25 = df[(df.year == 2025) & (df.month == 1)]["demand_gwh"].iloc[0]
    jan27 = df[(df.year == 2027) & (df.month == 1)]["demand_gwh"].iloc[0]
    assert jan27 == pytest.approx(jan25 * 1.03 ** 2)
    assert set(df[df.year > 2025]["source"]) == {"M4_extrapolation"}


def test_interval_basis_requires_intervals():
    legacy = M1InputAdapter(SourceSpec("m1", ROOT / "data/mock/legacy/m1_forecast.csv", DataStatus.MOCK, "v"), ROOT).load()
    with pytest.raises(DemandPathwayError, match="prediction intervals"):
        build_demand_pathway(legacy, ScenarioConfig(name="dd test", demand=DemandAssumptions(basis="upper_80")))


def test_efficiency_and_upper_bound(bundle):
    base, _ = build_demand_pathway(bundle.demand, ScenarioConfig(name="dd test"))
    eff, _ = build_demand_pathway(bundle.demand, ScenarioConfig(name="dd test", demand=DemandAssumptions(efficiency_savings_pct_by_end=10)))
    up, _ = build_demand_pathway(bundle.demand, ScenarioConfig(name="dd test", demand=DemandAssumptions(basis="upper_80")))
    b40 = base[base.year == 2040]["demand_gwh"].sum()
    assert eff[eff.year == 2040]["demand_gwh"].sum() == pytest.approx(0.9 * b40)
    assert up[up.year == 2040]["demand_gwh"].sum() > b40


def test_planning_start_before_m1_rejected(bundle):
    legacy = M1InputAdapter(SourceSpec("m1", ROOT / "data/mock/legacy/m1_forecast.csv", DataStatus.MOCK, "v"), ROOT).load()
    # legacy data starts in 2025: fine; a dataset starting in 2026 must reject a 2025 start
    legacy.monthly["year"] = legacy.monthly["year"] + 1
    with pytest.raises(DemandPathwayError, match="precedes"):
        build_demand_pathway(legacy, ScenarioConfig(name="dd test", end_year=2027))


def test_system_inputs_units(bundle):
    inp = build_system_inputs(bundle, ScenarioConfig(name="uu test", end_year=2026))
    assert inp.hours[(2025, 2)] == 672 and inp.hours[(2028 if False else 2026, 1)] == 744
    # potential = MW x factor x h / 1000
    assert inp.potential_gwh("solar", 2025, 7, 1000) == pytest.approx(1000 * 0.275 * 744 / 1000)
    assert inp.potential_gwh("gas", 2025, 1, 1000) == pytest.approx(1000 * 0.943 * 744 / 1000)
    assert inp.baseline_emissions_mt == pytest.approx(16000 * 0.457 / 1000)
    assert inp.crf(inp.techs["solar"]) == pytest.approx(0.08 * 1.08 ** 25 / (1.08 ** 25 - 1))


def test_retirements_applied(bundle):
    inp = build_system_inputs(bundle, ScenarioConfig(name="rr test"))
    assert inp.existing_mw[("gas", 2029)] == 3500
    assert inp.existing_mw[("gas", 2030)] == 3200
    assert inp.existing_mw[("gas", 2035)] == 2800


def test_unknown_technology_rejected(bundle):
    sc = ScenarioConfig(name="xx test", constraints=ConstraintSet(technology_limits=[TechnologyLimit(technology="fusion")]))
    with pytest.raises(ScenarioDataError, match="fusion"):
        build_system_inputs(bundle, sc)


def test_simulation_energy_balance(bundle, short_scenario):
    inp = build_system_inputs(bundle, short_scenario)
    res = simulate(inp)
    for mo in res["monthly"]:
        supplied = sum(mo["generation_gwh"].values()) + mo["imports_gwh"] + mo["unserved_gwh"]
        assert supplied == pytest.approx(mo["supply_requirement_gwh"], abs=0.02)
    for a in res["annual"]:
        assert a["imports_gwh"] <= inp.max_import_share * a["supply_requirement_gwh"] + 0.1
        vre = a["generation_gwh"]["solar"] + a["generation_gwh"]["wind"]
        assert vre <= inp.max_vre_share * a["supply_requirement_gwh"] + 0.1


def test_existing_fleet_shortfall_is_reported(bundle):
    res = simulate(build_system_inputs(bundle, ScenarioConfig(name="no investment")))
    assert res["summary"]["total_unserved_gwh"] > 0
    assert not res["adequate"] and res["adequacy_issues"]


def test_simulated_capacity_plan_reduces_shortfall(bundle):
    inp = build_system_inputs(bundle, ScenarioConfig(name="plan"))
    base = simulate(inp)["summary"]["total_unserved_gwh"]
    plan = {("gas", 2026): 1500.0, ("solar", 2026): 2000.0}
    after = simulate(inp, plan)
    assert after["summary"]["total_unserved_gwh"] < base
    assert after["summary"]["total_investment_musd"] == pytest.approx(1500 * 0.9 + 2000 * 0.75)


def test_simulation_rejects_negative_plan(bundle, short_scenario):
    with pytest.raises(ValueError):
        simulate(build_system_inputs(bundle, short_scenario), {("gas", 2025): -5})
