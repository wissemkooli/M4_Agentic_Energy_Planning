"""Monthly demand preparation (functionality of the original M4 prototype).

Converts monthly energy (GWh) into average power (MW):

    average_demand_mw = forecast_gwh x 1000 / (days_in_month x 24)

(1 GWh = 1000 MWh; dividing MWh by the hours of the month gives the average MW.)
"""
from __future__ import annotations

import pandas as pd


def hours_in_month(year: int, month: int) -> int:
    return pd.Period(f"{year}-{month:02d}", freq="M").days_in_month * 24


def gwh_to_average_mw(gwh: float, hours: float) -> float:
    return gwh * 1000.0 / hours


def prepare_demand(df: pd.DataFrame, value_column: str = "forecast_gwh") -> pd.DataFrame:
    out = df.copy()
    out["month"] = pd.to_datetime(out["month"].astype(str), format="%Y-%m")
    out = out.sort_values("month").reset_index(drop=True)
    out["year"] = out["month"].dt.year
    out["month_num"] = out["month"].dt.month
    out["days_in_month"] = out["month"].dt.days_in_month
    out["hours_in_month"] = out["days_in_month"] * 24
    out["average_demand_mw"] = out[value_column] * 1000 / out["hours_in_month"]
    return out
