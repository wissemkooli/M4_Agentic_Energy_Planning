"""Demand pathway construction for a scenario.

Monthly demand for each planning month comes from (in this order):
1. M1 forecasts (column chosen by `demand.basis`: point forecast or interval bound);
2. M4 extrapolation for years after the last complete M1 forecast year:
       D[y, m] = D[y_last, m] x (1 + g) ** (y - y_last)
   with g = `extrapolation_growth_pct` or, if not set, the CAGR of the last 3 complete
   M1 forecast years. The monthly profile of the last M1 year is kept.

Then scenario adjustments are applied:
       D_adj[y, m] = D[y, m] x multiplier x (1 - s x (y - y0 + 1) / (yN - y0 + 1))
where s = efficiency savings reached in the final year (linear ramp).

Every month is labelled with its source so forecasts are never confused with
measurements, and M4 extrapolations are never presented as M1 output.
"""
from __future__ import annotations

import pandas as pd

from ..data_integration.adapters import DemandForecastData
from ..data_integration.prepare_demand import hours_in_month
from ..scenarios.schema import ScenarioConfig


class DemandPathwayError(ValueError):
    pass


def _cagr(annual: pd.Series, years: int = 3) -> float:
    s = annual.sort_index()
    if len(s) < 2:
        return 0.0
    n = min(years, len(s) - 1)
    return float((s.iloc[-1] / s.iloc[-1 - n]) ** (1 / n) - 1)


def build_demand_pathway(demand: DemandForecastData, scenario: ScenarioConfig) -> tuple[pd.DataFrame, dict]:
    basis = scenario.demand.basis
    col = "forecast_gwh" if basis == "forecast" else basis
    m = demand.monthly
    if basis != "forecast" and not demand.has_intervals:
        raise DemandPathwayError(f"Demand basis '{basis}' requires M1 prediction intervals, which are not available.")
    fc = m[m["is_forecast"]].copy()
    full_years = demand.forecast_years()
    if not full_years:
        raise DemandPathwayError("M1 data contains no complete forecast year (12 months).")
    y0, yN = scenario.start_year, scenario.end_year
    first_fc, last_fc = min(full_years), max(full_years)
    if y0 < first_fc:
        raise DemandPathwayError(
            f"Planning start {y0} precedes the first complete M1 forecast year {first_fc}.")
    gaps = [y for y in range(y0, min(yN, last_fc) + 1) if y not in full_years]
    if gaps:
        raise DemandPathwayError(f"M1 forecasts have missing months in planning year(s) {gaps}.")

    annual = fc[fc["year"].isin(full_years)].groupby("year")["forecast_gwh"].sum()
    growth = (scenario.demand.extrapolation_growth_pct / 100.0
              if scenario.demand.extrapolation_growth_pct is not None else _cagr(annual))
    last = fc[fc["year"] == last_fc].set_index("month_num")

    rows = []
    for y in range(y0, yN + 1):
        for mo in range(1, 13):
            if y <= last_fc:
                r = fc[(fc["year"] == y) & (fc["month_num"] == mo)].iloc[0]
                base = float(r[col])
                point = float(r["forecast_gwh"])
                src = "M1_forecast"
            else:
                point = float(last.loc[mo, "forecast_gwh"]) * (1 + growth) ** (y - last_fc)
                ratio = float(last.loc[mo, col]) / float(last.loc[mo, "forecast_gwh"])
                base = point * ratio
                src = "M4_extrapolation"
            ramp = (y - y0 + 1) / (yN - y0 + 1)
            eff = 1 - scenario.demand.efficiency_savings_pct_by_end / 100.0 * ramp
            demand_gwh = base * scenario.demand.demand_multiplier * eff
            h = hours_in_month(y, mo)
            rows.append({
                "year": y, "month": mo, "hours": h, "m1_point_forecast_gwh": round(point, 3),
                "basis_gwh": round(base, 3), "efficiency_factor": round(eff, 5),
                "demand_gwh": demand_gwh, "average_demand_mw": demand_gwh * 1000 / h, "source": src,
            })
    df = pd.DataFrame(rows)
    meta = {
        "basis": basis,
        "m1_full_forecast_years": [first_fc, last_fc],
        "extrapolated_years": sorted({int(y) for y in df.loc[df["source"] == "M4_extrapolation", "year"]}),
        "extrapolation_growth_pct": round(growth * 100, 3),
        "extrapolation_growth_source": "scenario" if scenario.demand.extrapolation_growth_pct is not None
        else "CAGR of last 3 complete M1 forecast years",
        "efficiency_savings_pct_by_end": scenario.demand.efficiency_savings_pct_by_end,
        "demand_multiplier": scenario.demand.demand_multiplier,
    }
    return df, meta
