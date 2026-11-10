"""Short-term operational problems: MILP model, validation, infeasibility diagnosis, strategies."""
import pytest
from pydantic import ValidationError

from m4.modeling.system import ScenarioDataError
from m4.operations.engine import run_operational, simulate_status_quo
from m4.operations.inputs import build_operational_inputs
from m4.operations.schema import OperationalProblem, OpObjectiveConfig
from m4.operations.strategies import generate_operational_strategies


def _inp(bundle, **kw):
    return build_operational_inputs(bundle, OperationalProblem(name="test problem", **kw))


def test_schema_validation():
    with pytest.raises(ValidationError):
        OperationalProblem(name="bad month", start_month="2025-13")
    with pytest.raises(ValidationError):
        OperationalProblem(name="before reference", start_month="2024-06")
    with pytest.raises(ValidationError):
        OperationalProblem(name="too long", start_month="2025-01", months=30)
    with pytest.raises(ValidationError):
        OpObjectiveConfig(method="lexicographic", priorities=["min_gas"])
    p = OperationalProblem(name="wrap", start_month="2025-11", months=3)
    assert p.periods == [(2025, 11), (2025, 12), (2026, 1)]


def test_inputs_use_m1_and_outages(bundle):
    inp = _inp(bundle, start_month="2025-01", months=2, demand_change_pct=10,
               capacity_outages=[{"technology": "gas", "unavailable_mw": 600}])
    m = bundle.demand.monthly.set_index("month")
    assert inp.demand_m1_gwh[(2025, 1)] == pytest.approx(float(m.loc["2025-01", "forecast_gwh"]))
    assert inp.demand_gwh[(2025, 1)] == pytest.approx(inp.demand_m1_gwh[(2025, 1)] * 1.10)
    assert inp.units_total[("gas", (2025, 1))] == 14
    assert inp.units_available[("gas", (2025, 1))] == 11      # 600 MW -> 3 units of 250 MW (rounded up)
    with pytest.raises(ScenarioDataError):
        _inp(bundle, start_month="2025-01", capacity_outages=[{"technology": "nuclear", "unavailable_mw": 1}])


def test_milp_optimal_integer_and_validated(bundle):
    inp = _inp(bundle, start_month="2025-01", months=3)
    r = run_operational(inp)
    assert r["status"] == "optimal" and r["feasible"]
    assert r["solver"]["problem_class"] == "MILP"
    assert r["validation"]["status"] == "valid"
    checks = {c["check"]: c["passed"] for c in r["validation"]["physical_checks"]}
    assert checks["Integer number of units in service"] and checks["Minimum stable output of units in service"]
    for mo in r["results"]["monthly"]:
        n = mo["units_in_service"]["gas"]
        assert n == int(n) and 0 <= n <= mo["units_available"]["gas"]
        assert mo["reserve_margin_achieved"] >= inp.reserve_margin - 1e-6
        gas_max = n * inp.unit_size_mw * inp.techs["gas"].availability * mo["hours"] / 1000
        assert inp.min_stable * gas_max - 1e-3 <= mo["generation_gwh"]["gas"] <= gas_max + 1e-3
    # the optimizer never does worse than keeping every unit in service
    sq = simulate_status_quo(inp)
    assert r["results"]["summary"]["total_economic_cost_musd"] <= sq["summary"]["total_economic_cost_musd"] + 1e-6


def test_lp_relaxation_option(bundle):
    inp = _inp(bundle, start_month="2025-01", unit_commitment=False)
    r = run_operational(inp)
    assert r["solver"]["problem_class"] == "LP" and r["feasible"]
    milp = run_operational(_inp(bundle, start_month="2025-01"))
    assert r["objective_values"]["min_operating_cost"] <= milp["objective_values"]["min_operating_cost"] + 1e-6


def test_infeasible_reserve_is_diagnosed(bundle):
    inp = _inp(bundle, start_month="2025-07", months=2, capacity_outages=[{"technology": "gas", "unavailable_mw": 800}])
    r = run_operational(inp)
    assert r["status"] == "infeasible" and r["results"] is None
    v = r["diagnostics"]["violations"]
    assert v and v[0]["constraint"] == "reserve" and v[0]["periods"]
    assert "Operating reserve" in r["message"]


def test_unserved_energy_when_gas_is_limited(bundle):
    inp = _inp(bundle, start_month="2025-01", gas_generation_limit_gwh=1000, operating_reserve_pct=0)
    r = run_operational(inp)
    assert r["feasible"]
    s = r["results"]["summary"]
    assert s["gas_gwh"] <= 1000 + 1e-3
    assert s["unserved_gwh"] > 0      # reported, never hidden
    rows = {x["constraint"]: x for x in r["validation"]["constraints"]}
    assert rows["Gas generation limit"]["satisfied"]
    not_allowed = run_operational(_inp(bundle, start_month="2025-01", gas_generation_limit_gwh=1000, operating_reserve_pct=0,
                                       allow_unserved_energy=False))
    assert not_allowed["status"] == "infeasible"
    assert any(v["constraint"] in ("gas_limit", "unserved_energy") for v in not_allowed["diagnostics"]["violations"])


def test_operational_strategies_distinct_and_validated(bundle):
    out = generate_operational_strategies(bundle, OperationalProblem(name="three months", start_month="2025-01", months=3,
                                                                     demand_change_pct=5))
    assert out["status"] == "ok" and out["problem_type"] == "operational"
    keys = [s["key"] for s in out["strategies"]]
    assert keys[:3] == ["least_cost_operations", "reliability_first", "min_gas_use"]
    by = {s["key"]: s for s in out["strategies"]}
    for s in out["strategies"]:
        assert s["feasible"] and s["run"]["validation"]["status"] == "valid"
        assert s["report"]["kind"] == "operational" and s["report"]["schedule"]
    lc, mg = by["least_cost_operations"]["report"]["headline"], by["min_gas_use"]["report"]["headline"]
    assert mg["gas_gwh"] < lc["gas_gwh"] and mg["operating_cost_musd"] > lc["operating_cost_musd"]
    assert mg["unserved_gwh"] <= lc["unserved_gwh"] + 1e-3
    rf = by["reliability_first"]["report"]["headline"]
    assert rf["min_reserve_margin"] >= lc["min_reserve_margin"] - 1e-9
    if "balanced_operations" in by:
        b = by["balanced_operations"]["report"]["headline"]
        assert mg["gas_gwh"] - 1e-3 <= b["gas_gwh"] <= lc["gas_gwh"] + 1e-3
    assert out["status_quo"]["summary"]["operating_cost_musd"] >= lc["operating_cost_musd"] - 1e-6


def test_operational_strategies_infeasible_reported(bundle):
    out = generate_operational_strategies(bundle, OperationalProblem(name="impossible", start_month="2025-07",
                                                                     capacity_outages=[{"technology": "gas", "unavailable_mw": 1500}]))
    assert out["status"] == "infeasible" and out["strategies"] == [] and out["diagnostics"]["violations"]
