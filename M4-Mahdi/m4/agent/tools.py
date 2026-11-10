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
from ..operations.engine import simulate_status_quo
from ..operations.inputs import build_operational_inputs
from ..operations.schema import OperationalProblem
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


# ------------------------------------------------------------------ short-term operational tools
def _op_problem(start_month: str, months: int = 1, demand_basis: str = "forecast", demand_change_pct: float = 0.0,
                capacity_outages: list | None = None, operating_reserve_pct: float | None = None,
                gas_generation_limit_gwh: float | None = None) -> OperationalProblem:
    return OperationalProblem(name="Investigation", start_month=start_month, months=int(months or 1),
                              demand_basis=demand_basis or "forecast", demand_change_pct=float(demand_change_pct or 0),
                              capacity_outages=capacity_outages or [], operating_reserve_pct=operating_reserve_pct,
                              gas_generation_limit_gwh=gas_generation_limit_gwh)


def get_monthly_demand(bundle: InputBundle, start_month: str, months: int = 1, **_) -> dict:
    """M1 monthly demand for the operating period, with interval bounds and the same month one year earlier."""
    m = bundle.demand.monthly.set_index("month")
    p = _op_problem(start_month, months)
    rows = []
    for y, mo in p.periods:
        key = f"{y}-{mo:02d}"
        if key not in m.index:
            rows.append({"month": key, "available": False})
            continue
        r = m.loc[key]
        prev = f"{y - 1}-{mo:02d}"
        prev_val = None
        if prev in m.index:
            pr = m.loc[prev]
            prev_val = float(pr["actual_gwh"]) if pr["actual_gwh"] == pr["actual_gwh"] and pr["actual_gwh"] is not None \
                else float(pr["forecast_gwh"])
        row = {"month": key, "available": True, "is_forecast": bool(r["is_forecast"]),
               "forecast_gwh": round(float(r["forecast_gwh"]), 1), "same_month_previous_year_gwh": round(prev_val, 1) if prev_val else None}
        for c in ("lower_80", "upper_80", "lower_95", "upper_95"):
            if c in r.index and r[c] == r[c]:
                row[c] = round(float(r[c]), 1)
        if prev_val:
            row["change_vs_previous_year_pct"] = round(100 * (row["forecast_gwh"] / prev_val - 1), 2)
        rows.append(row)
    return {"data_status": bundle.demand.provenance.data_status.value, "dataset_version": bundle.demand.provenance.dataset_version,
            "latest_actual_month": _latest_actual(bundle), "first_forecast_month": bundle.demand.first_forecast_month,
            "months": rows}


def _latest_actual(bundle: InputBundle) -> str | None:
    m = bundle.demand.monthly
    act = m[m["actual_gwh"].notna()]
    return str(act["month"].max()) if len(act) else None


def get_operational_fleet(bundle: InputBundle, start_month: str, months: int = 1, capacity_outages: list | None = None,
                          demand_change_pct: float = 0.0, operating_reserve_pct: float | None = None, **_) -> dict:
    """Units and capacity available in the period, peak demand and the highest reserve margin achievable."""
    inp = build_operational_inputs(bundle, _op_problem(start_month, months, demand_change_pct=demand_change_pct,
                                                       capacity_outages=capacity_outages,
                                                       operating_reserve_pct=operating_reserve_pct))
    rows = []
    for p in inp.periods:
        all_units = {(t, p): inp.units_available[(t, p)] for t in inp.dispatchable}
        firm = inp.firm_capacity_mw(all_units, p)
        rows.append({"month": inp.labels[p], "peak_mw": round(inp.peak_mw[p], 1), "firm_capacity_all_units_mw": round(firm, 1),
                     "max_reserve_margin": round(firm / inp.peak_mw[p] - 1, 4),
                     "units_available": {t: inp.units_available[(t, p)] for t in inp.dispatchable},
                     "units_total": {t: inp.units_total[(t, p)] for t in inp.dispatchable},
                     "capacity_in_service_mw": {t: round(inp.capacity_mw[(t, p)], 1) for t in inp.techs}})
    return {"data_status": inp.data_status, "unit_size_mw": inp.unit_size_mw, "min_stable_fraction": inp.min_stable,
            "required_reserve_margin": inp.reserve_margin, "months": rows,
            "max_achievable_reserve_margin": min(r["max_reserve_margin"] for r in rows),
            "note": "Firm capacity counts every available gas unit plus the capacity credit of the other technologies."}


def simulate_status_quo_operation(bundle: InputBundle, start_month: str, months: int = 1, capacity_outages: list | None = None,
                                  demand_change_pct: float = 0.0, demand_basis: str = "forecast",
                                  operating_reserve_pct: float | None = None, gas_generation_limit_gwh: float | None = None,
                                  **_) -> dict:
    """Merit-order operation with every available unit in service (reference before optimization)."""
    inp = build_operational_inputs(bundle, _op_problem(start_month, months, demand_basis, demand_change_pct, capacity_outages,
                                                       operating_reserve_pct, gas_generation_limit_gwh))
    res = simulate_status_quo(inp)
    return {"method": res["method"], "summary": res["summary"], "issues": res["issues"], "data_status": res["data_status"]}


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
    Tool("get_monthly_demand", "M1 monthly demand for a short operating period (with interval bounds and previous year).",
         {"type": "object", "properties": {"start_month": {"type": "string", "description": "YYYY-MM"},
                                           "months": {"type": "integer"}}, "required": ["start_month"]}, get_monthly_demand),
    Tool("get_operational_fleet", "Gas units and capacity available in an operating period, peak demand and maximum reserve margin.",
         {"type": "object", "properties": {"start_month": {"type": "string"}, "months": {"type": "integer"}},
          "required": ["start_month"]}, get_operational_fleet),
    Tool("simulate_status_quo_operation", "Simulate operating the period with every available unit in service (merit order).",
         {"type": "object", "properties": {"start_month": {"type": "string"}, "months": {"type": "integer"}},
          "required": ["start_month"]}, simulate_status_quo_operation),
    Tool("formulate_optimization_problem", "Describe the mathematical formulation of a scenario.",
         {"type": "object", "properties": {"scenario": {"type": "object"}}, "required": ["scenario"]},
         formulate_optimization_problem, investigation=False),
]}

MANDATORY_INVESTIGATION = ["get_reference_energy_system", "get_demand_forecast", "get_technology_assumptions",
                           "check_data_quality"]
MANDATORY_OPERATIONAL = ["get_reference_energy_system", "get_monthly_demand", "get_technology_assumptions",
                         "check_data_quality", "get_operational_fleet", "simulate_status_quo_operation"]
OPERATIONAL_TOOLS = {"get_monthly_demand", "get_operational_fleet", "simulate_status_quo_operation"}
PLANNING_ONLY_TOOLS = {"simulate_existing_fleet", "estimate_unconstrained_investment", "get_demand_forecast"}


def call_tool(bundle: InputBundle, name: str, args: dict | None = None) -> dict:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool '{name}'")
    return TOOLS[name].fn(bundle, **(args or {}))


def tool_catalog() -> list[dict]:
    return [{"name": t.name, "description": t.description, "parameters": t.parameters,
             "available_to_llm_during_investigation": t.investigation} for t in TOOLS.values()]
