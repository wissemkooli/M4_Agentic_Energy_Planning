"""Agent tools: thin wrappers that execute real M4 Python logic and return JSON.

Investigation tools are read-only (data inspection, simulation of the existing fleet,
reference least-cost estimate). Optimization tools are only called by the workflow AFTER
human approval; the LLM is never allowed to call them directly.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..data_integration.quality import data_quality_report
from ..data_integration.repository import InputBundle
from ..modeling.simulation import simulate
from ..modeling.system import build_system_inputs
from ..optimization.engine import run_optimization
from ..optimization.formulation import describe_formulation
from ..scenarios.schema import ConstraintSet, ObjectiveConfig, ScenarioConfig


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]
    investigation: bool = True  # may be selected by the LLM during investigation

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def _horizon(args: dict) -> tuple[int, int]:
    return int(args.get("start_year") or 2025), int(args.get("end_year") or 2040)


def get_reference_energy_system(bundle: InputBundle, **_) -> dict:
    r = bundle.reference
    return {"reference_year": r.reference_year, "data_status": r.provenance.data_status.value,
            "totals": r.totals,
            "technologies": r.technologies[["technology", "installed_capacity_mw", "avg_available_capacity_mw",
                                            "unavailable_capacity_mw", "generation_gwh",
                                            "observed_capacity_factor"]].round(4).to_dict(orient="records")}


def get_demand_forecast(bundle: InputBundle, start_year: int | None = None, end_year: int | None = None, **_) -> dict:
    y0, y1 = int(start_year or 2025), int(end_year or 2040)
    m = bundle.demand.monthly
    f = m[m["is_forecast"]]
    annual = f.groupby("year").agg(forecast_gwh=("forecast_gwh", "sum"), lower_80=("lower_80", "sum"),
                                   upper_80=("upper_80", "sum")).round(1)
    annual = annual[(annual.index >= y0) & (annual.index <= y1)]
    hist = m[m["actual_gwh"].notna()].groupby("year")["actual_gwh"].sum().round(1)
    return {"data_status": bundle.demand.provenance.data_status.value,
            "dataset_version": bundle.demand.provenance.dataset_version,
            "model_label": str(m["model"].iloc[-1]), "forecast_years": bundle.demand.forecast_years(),
            "annual_forecast_gwh": {int(k): v for k, v in annual["forecast_gwh"].items()},
            "annual_upper_80_gwh": {int(k): v for k, v in annual["upper_80"].items()},
            "historical_actual_gwh": {int(k): v for k, v in hist.items()},
            "peak_month_average_mw": round(float(f["average_demand_mw"].max()), 1)}


def get_international_benchmark(bundle: InputBundle, indicators: list[str] | None = None, **_) -> dict:
    b = bundle.benchmark
    wanted = indicators or b.indicators
    out = {}
    for ind in wanted:
        if ind in b.indicators:
            s = b.indicator_summary(ind)
            s.pop("countries", None)
            out[ind] = s
    return {"data_status": b.provenance.data_status.value, "dataset_version": b.provenance.dataset_version,
            "note": "Benchmark indicators give context only; they are not technology cost or capacity-factor inputs.",
            "indicators": out, "available_indicators": b.indicators}


def get_technology_assumptions(bundle: InputBundle, **_) -> dict:
    a = bundle.assumptions
    cols = ["technology", "category", "is_renewable", "capex_musd_per_mw", "fixed_om_musd_per_mw_yr",
            "variable_cost_usd_per_mwh", "emission_factor_t_per_mwh", "lifetime_years", "availability_factor",
            "capacity_credit", "max_annual_build_mw", "max_total_new_mw"]
    return {"data_status": a.provenance.data_status.value, "currency": a.currency,
            "technologies": a.technologies[cols].to_dict(orient="records"),
            "system_parameters": {k: v["value"] for k, v in a.system.items()},
            "source": "data/input/assumptions (independent of M3)"}


def check_data_quality(bundle: InputBundle, start_year: int | None = None, end_year: int | None = None, **_) -> dict:
    return data_quality_report(bundle, int(start_year or 2025), int(end_year or 2040))


def simulate_existing_fleet(bundle: InputBundle, start_year: int | None = None, end_year: int | None = None,
                            demand_basis: str = "forecast", **_) -> dict:
    sc = ScenarioConfig(name="Existing fleet (no new investment)", start_year=int(start_year or 2025),
                        end_year=int(end_year or 2040), demand={"basis": demand_basis})
    res = simulate(build_system_inputs(bundle, sc))
    first_gap = next((a["year"] for a in res["annual"] if a["unserved_gwh"] > 1e-3), None)
    first_rm = next((a["year"] for a in res["annual"] if a["reserve_margin_achieved"] < 0.15), None)
    return {"method": "merit-order simulation, no new capacity", "summary": res["summary"],
            "first_year_with_unserved_energy": first_gap, "first_year_below_reserve_margin": first_rm,
            "adequacy_issues": res["adequacy_issues"][:6]}


def estimate_unconstrained_investment(bundle: InputBundle, start_year: int | None = None,
                                      end_year: int | None = None, reserve_margin_pct: float | None = None, **_) -> dict:
    """Least-cost plan with no budget limit: a factual reference point for budget questions."""
    sc = ScenarioConfig(name="Least-cost reference (no budget limit)", start_year=int(start_year or 2025),
                        end_year=int(end_year or 2040),
                        constraints=ConstraintSet(reserve_margin_pct=reserve_margin_pct),
                        objective=ObjectiveConfig(method="single", primary="min_cost"))
    run = run_optimization(build_system_inputs(bundle, sc), diagnose=False)
    if not run["feasible"]:
        return {"status": run["status"], "message": run.get("message")}
    s = run["results"]["summary"]
    return {"status": "optimal", "total_investment_musd": s["total_investment_musd"],
            "total_cost_npv_musd": s["total_cost_npv_musd"], "total_new_capacity_mw": s["total_new_capacity_mw"],
            "renewable_share_final_year": s["renewable_share_final_year"],
            "final_year_emissions_mt": s["final_year_emissions_mt"],
            "note": "Computed by the optimizer without any budget constraint (reference point only)."}


def formulate_optimization_problem(bundle: InputBundle, scenario: dict, **_) -> dict:
    return describe_formulation(build_system_inputs(bundle, ScenarioConfig.model_validate(scenario)))


_H = {"start_year": {"type": "integer"}, "end_year": {"type": "integer"}}

TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_reference_energy_system", "2024 reference system: installed/available capacity and generation by technology.",
         {"type": "object", "properties": {}}, get_reference_energy_system),
    Tool("get_demand_forecast", "M1 monthly demand forecasts aggregated per year, with uncertainty bounds.",
         {"type": "object", "properties": _H}, get_demand_forecast),
    Tool("get_international_benchmark", "M3 international benchmark indicators (Tunisia vs comparators).",
         {"type": "object", "properties": {"indicators": {"type": "array", "items": {"type": "string"}}}},
         get_international_benchmark),
    Tool("get_technology_assumptions", "Technology costs, emission factors, capacity factors and build limits.",
         {"type": "object", "properties": {}}, get_technology_assumptions),
    Tool("check_data_quality", "Data quality report: MOCK/REAL status, missing data, inconsistencies, M1 coverage.",
         {"type": "object", "properties": _H}, check_data_quality),
    Tool("simulate_existing_fleet", "Simulate the existing fleet without new investment to quantify supply gaps.",
         {"type": "object", "properties": {**_H, "demand_basis": {"type": "string"}}}, simulate_existing_fleet),
    Tool("estimate_unconstrained_investment",
         "Run the least-cost optimization without budget limit to obtain a reference investment requirement.",
         {"type": "object", "properties": {**_H, "reserve_margin_pct": {"type": "number"}}},
         estimate_unconstrained_investment),
    Tool("formulate_optimization_problem", "Describe the mathematical formulation of a scenario.",
         {"type": "object", "properties": {"scenario": {"type": "object"}}, "required": ["scenario"]},
         formulate_optimization_problem, investigation=False),
]}

MANDATORY_INVESTIGATION = ["get_reference_energy_system", "get_demand_forecast", "get_technology_assumptions",
                           "check_data_quality"]


def call_tool(bundle: InputBundle, name: str, args: dict | None = None) -> dict:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool '{name}'")
    return TOOLS[name].fn(bundle, **(args or {}))


def tool_catalog() -> list[dict]:
    return [{"name": t.name, "description": t.description, "parameters": t.parameters,
             "available_to_llm_during_investigation": t.investigation} for t in TOOLS.values()]
