"""Health, dashboard and input-data endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...agent.llm import LLMClient
from ...auth.deps import get_current_user
from ...config import get_settings
from ...data_integration.quality import data_quality_report
from ...data_integration.repository import get_inputs
from ...db.models import OptimizationRun, Strategy, User
from ...db.session import get_db
from ...optimization.solver import SolverUnavailableError, get_solver
from ...services import run_summary, strategy_dict

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/health")
def health():
    """Public liveness/readiness check (no secrets)."""
    s = get_settings()
    try:
        solver = get_solver()[0]
    except SolverUnavailableError:
        solver = None
    data_ok, data_status, err = True, None, None
    try:
        data_status = get_inputs().overall_status.value
    except Exception as exc:
        data_ok, err = False, str(exc)
    return {"status": "ok" if data_ok and solver else "degraded", "app": s.app_name, "environment": s.app_env,
            "solver": solver, "data_loaded": data_ok, "data_status": data_status, "data_error": err,
            "llm_configured": LLMClient().available,
            "database": s.resolved_database_url.split(":")[0]}


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    b = get_inputs()
    ref = b.reference
    techs = ref.technologies
    ef = dict(zip(b.assumptions.technologies["technology"], b.assumptions.technologies["emission_factor_t_per_mwh"]))
    ren = set(b.assumptions.technologies.loc[b.assumptions.technologies["is_renewable"], "technology"])
    gen_total = ref.totals["total_generation_gwh"]
    re_gen = float(techs.loc[techs["technology"].isin(ren), "generation_gwh"].sum())
    gas_gen = float(techs.loc[techs["technology"].str.contains("gas"), "generation_gwh"].sum())
    emissions = float(sum(r["generation_gwh"] * ef.get(r["technology"], 0) / 1000 for _, r in techs.iterrows()))

    m = b.demand.monthly
    fc = m[m["is_forecast"]]
    annual = fc.groupby("year").agg(forecast_gwh=("forecast_gwh", "sum"), lower_80=("lower_80", "sum"),
                                    upper_80=("upper_80", "sum"), lower_95=("lower_95", "sum"),
                                    upper_95=("upper_95", "sum"), peak_avg_mw=("average_demand_mw", "max")).round(1)
    hist = m[m["actual_gwh"].notna()].groupby("year")["actual_gwh"].sum().round(1)
    first_year = int(fc["year"].min()) if len(fc) else None
    monthly_first = fc[fc["year"] == first_year][["month", "forecast_gwh", "lower_80", "upper_80", "average_demand_mw"]]
    quality = data_quality_report(b)

    bench = []
    for ind in b.benchmark.indicators:
        s = b.benchmark.indicator_summary(ind)
        s.pop("countries")
        bench.append(s)

    q = select(OptimizationRun).order_by(OptimizationRun.created_at.desc()).limit(5)
    if user.role != "ADMIN":
        q = q.where(OptimizationRun.owner_id == user.id)
    ref_strategy = db.scalar(select(Strategy).where(Strategy.is_reference.is_(True)))
    years = sorted(annual.index)
    growth = None
    if len(years) > 1:
        growth = round(100 * ((annual.loc[years[-1], "forecast_gwh"] / annual.loc[years[0], "forecast_gwh"])
                              ** (1 / (years[-1] - years[0])) - 1), 2)
    return {
        "data_status": b.overall_status.value,
        "provenance": b.provenance_dicts(),
        "reference_year": ref.reference_year,
        "reference": {"technologies": techs.round(4).to_dict(orient="records"), "totals": ref.totals},
        "kpis": {
            "installed_capacity_mw": ref.totals["total_installed_capacity_mw"],
            "available_capacity_mw": ref.totals["total_available_capacity_mw"],
            "unavailable_capacity_mw": ref.totals["total_unavailable_capacity_mw"],
            "generation_gwh": gen_total,
            "renewable_share_generation": round(re_gen / gen_total, 4) if gen_total else None,
            "gas_share_generation": round(gas_gen / gen_total, 4) if gen_total else None,
            "emissions_mt": round(emissions, 3),
            "emission_intensity_g_per_kwh": round(emissions * 1e6 / gen_total, 1) if gen_total else None,
            "first_forecast_year": first_year,
            "first_forecast_year_demand_gwh": float(annual.loc[first_year, "forecast_gwh"]) if first_year else None,
            "first_forecast_year_peak_avg_mw": float(annual.loc[first_year, "peak_avg_mw"]) if first_year else None,
            "forecast_cagr_pct": growth,
        },
        "demand": {
            "annual_forecast": [{"year": int(y), **{k: float(v) for k, v in r.items()}} for y, r in annual.iterrows()],
            "annual_actual": [{"year": int(y), "actual_gwh": float(v)} for y, v in hist.items()],
            "first_year_monthly": monthly_first.round(1).to_dict(orient="records"),
            "model_label": str(m["model"].iloc[-1]),
        },
        "benchmark": bench,
        "data_quality": {"n_errors": quality["n_errors"], "n_warnings": quality["n_warnings"],
                         "issues": quality["issues"][:6], "reconciliation_2024": quality["reconciliation_2024"]},
        "recent_runs": [run_summary(r) for r in db.scalars(q)],
        "approved_reference_strategy": strategy_dict(ref_strategy) if ref_strategy else None,
    }


@router.get("/energy/reference")
def reference(user: User = Depends(get_current_user)):
    r = get_inputs().reference
    return {"reference_year": r.reference_year, "technologies": r.technologies.round(4).to_dict(orient="records"),
            "totals": r.totals, "provenance": r.provenance.to_dict()}


@router.get("/forecast")
def forecast(user: User = Depends(get_current_user), start: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
             end: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    d = get_inputs().demand
    m = d.monthly
    if start:
        m = m[m["month"] >= start]
    if end:
        m = m[m["month"] <= end]
    return {"provenance": d.provenance.to_dict(), "has_intervals": d.has_intervals,
            "first_forecast_month": d.first_forecast_month, "missing_months": d.missing_months,
            "records": m.astype(object).where(m.notna(), None).to_dict(orient="records")}


@router.get("/benchmark")
def benchmark(user: User = Depends(get_current_user), indicator: str | None = None):
    b = get_inputs().benchmark
    if indicator:
        if indicator not in b.indicators:
            raise HTTPException(404, f"Unknown indicator '{indicator}'. Available: {b.indicators}")
        return {"provenance": b.provenance.to_dict(), "summary": b.indicator_summary(indicator)}
    return {"provenance": b.provenance.to_dict(), "indicators": b.indicators, "tunisia_label": b.tunisia_label,
            "records": b.table.astype(object).where(b.table.notna(), None).to_dict(orient="records")}


@router.get("/data/sources")
def data_sources(user: User = Depends(get_current_user)):
    b = get_inputs()
    a = b.assumptions
    return {"overall_status": b.overall_status.value, "provenance": b.provenance_dicts(),
            "quality": data_quality_report(b),
            "assumptions": {
                "currency": a.currency,
                "technologies": a.technologies.to_dict(orient="records"),
                "capacity_factor_profiles": a.cf_profiles,
                "retirements": a.retirements.astype(object).where(a.retirements.notna(), None).to_dict(orient="records"),
                "system_parameters": a.system},
            "integration_note": ("Real M1/M3 outputs are integrated by editing data/input/sources.yml (path, "
                                 "data_status, dataset_version). See docs/DATA_INTEGRATION_GUIDE.md.")}
