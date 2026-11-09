"""Cross-dataset data quality report.

Used by the dashboard, the "Data sources & assumptions" page and the agent's
investigation phase. It never modifies data; it only reports issues.
"""
from __future__ import annotations

from .provenance import DataStatus
from .repository import InputBundle


def data_quality_report(bundle: InputBundle, start_year: int = 2025, end_year: int = 2040) -> dict:
    issues: list[dict] = []

    def add(level: str, source: str, message: str) -> None:
        issues.append({"level": level, "source": source, "message": message})

    for p in bundle.provenance:
        for w in p.warnings:
            add("warning", p.source, w)
        if p.data_status != DataStatus.REAL:
            add("info", p.source, f"Dataset status is {p.data_status.value}: results are not based on verified data.")

    statuses = {p.data_status for p in bundle.provenance}
    if DataStatus.REAL in statuses and DataStatus.MOCK in statuses:
        add("warning", "all", "MOCK and REAL inputs are mixed: every result will be labelled MIXED.")

    # M1 coverage vs planning horizon
    years = bundle.demand.forecast_years()
    m1_cover = [y for y in range(start_year, end_year + 1) if y in years]
    beyond = [y for y in range(start_year, end_year + 1) if y not in years]
    if beyond:
        add("warning", "m1_demand_forecast",
            f"M1 forecasts cover {len(m1_cover)} of {end_year - start_year + 1} planning years; "
            f"years {beyond[0]}-{beyond[-1]} require M4 extrapolation (configurable growth rate).")

    # Technology coverage
    ref_techs = set(bundle.reference.technologies["technology"])
    assumption_techs = set(bundle.assumptions.technologies["technology"])
    for t in sorted(ref_techs - assumption_techs):
        add("error", "technology_assumptions", f"Technology '{t}' exists in the reference system but has no assumptions.")
    for t in sorted(assumption_techs - ref_techs):
        add("info", "reference_2024", f"Technology '{t}' has no 2024 capacity: treated as 0 MW existing.")

    # 2024 balance check (reported, never forced)
    m = bundle.demand.monthly
    hist_2024 = m[(m["year"] == 2024) & m["actual_gwh"].notna()]
    ref_gen = bundle.reference.totals["total_generation_gwh"]
    reconciliation = None
    if len(hist_2024) == 12:
        demand_2024 = float(hist_2024["actual_gwh"].sum())
        gap = demand_2024 - ref_gen
        reconciliation = {
            "demand_2024_gwh": round(demand_2024, 1),
            "reference_generation_2024_gwh": round(ref_gen, 1),
            "gap_gwh": round(gap, 1),
            "gap_pct_of_demand": round(100 * gap / demand_2024, 2),
            "treatment": ("Not forced to balance. The 2024 reference defines the initial installed capacity only; "
                          "the monthly energy balance is enforced from the first planning year. The gap may "
                          "reflect imports, network losses or different demand definitions and must be "
                          "confirmed with the data owners."),
        }
        if abs(gap) / demand_2024 > 0.02:
            add("warning", "reference_2024",
                f"2024 M1 demand ({demand_2024:,.0f} GWh) differs from reference generation ({ref_gen:,.0f} GWh) "
                f"by {gap:,.0f} GWh ({100 * gap / demand_2024:.1f} %). Not forced to balance.")
    else:
        add("info", "m1_demand_forecast", "No complete 2024 actual demand in M1 data: 2024 balance check skipped.")

    # Observed capacity factors vs assumed profiles (calibration info)
    calib = []
    for _, r in bundle.reference.technologies.iterrows():
        t = r["technology"]
        prof = bundle.assumptions.cf_profiles.get(t)
        if prof:
            assumed = sum(prof.values()) / 12
            calib.append({"technology": t, "observed_cf_2024": round(float(r["observed_capacity_factor"]), 4),
                          "assumed_mean_cf": round(assumed, 4)})
            if abs(assumed - r["observed_capacity_factor"]) > 0.05:
                add("warning", "technology_assumptions",
                    f"{t}: assumed mean CF {assumed:.3f} differs from observed 2024 CF {r['observed_capacity_factor']:.3f}.")

    level_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: level_order[i["level"]])
    return {
        "overall_status": bundle.overall_status.value,
        "n_errors": sum(i["level"] == "error" for i in issues),
        "n_warnings": sum(i["level"] == "warning" for i in issues),
        "issues": issues,
        "m1_coverage": {"forecast_years": years, "planning_years_covered": m1_cover, "extrapolated_years": beyond,
                        "has_intervals": bundle.demand.has_intervals,
                        "first_forecast_month": bundle.demand.first_forecast_month,
                        "last_month": bundle.demand.last_month},
        "reconciliation_2024": reconciliation,
        "capacity_factor_calibration": calib,
    }
