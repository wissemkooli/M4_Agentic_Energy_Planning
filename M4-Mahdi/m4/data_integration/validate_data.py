"""Input validation rules.

Each function raises `DataValidationError` with an explicit message listing every
problem found, so the user knows exactly which file and which rows must be fixed.
The three checks of the original prototype (negative M1 forecast, capacity factor > 1,
available capacity > installed capacity) are preserved.
"""
from __future__ import annotations

import pandas as pd


class DataValidationError(ValueError):
    def __init__(self, label: str, problems: list[str]):
        self.label = label
        self.problems = problems
        super().__init__(f"{label}: " + "; ".join(problems))


def require_columns(df: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataValidationError(label, [f"missing required column(s): {', '.join(missing)}"])


def require_numeric(df: pd.DataFrame, columns: list[str], label: str, allow_na: bool = False) -> None:
    problems = []
    for c in columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        bad = conv.isna() & (df[c].notna() if allow_na else True)
        if bad.any():
            rows = list(df.index[bad][:5])
            problems.append(f"column '{c}' has non-numeric or missing values at rows {rows}")
    if problems:
        raise DataValidationError(label, problems)


def validate_m1_forecast(df: pd.DataFrame, label: str = "M1 demand forecast") -> None:
    require_columns(df, ["month", "forecast_gwh"], label)
    require_numeric(df, ["forecast_gwh"], label)
    problems = []
    months = pd.to_datetime(df["month"].astype(str), format="%Y-%m", errors="coerce")
    if months.isna().any():
        problems.append(f"invalid month format (expected YYYY-MM) at rows {list(df.index[months.isna()][:5])}")
    fc = pd.to_numeric(df["forecast_gwh"])
    if (fc <= 0).any():
        problems.append(f"forecast_gwh must be strictly positive (rows {list(df.index[fc <= 0][:5])})")
    dup = df["month"].astype(str).duplicated(keep=False)
    if dup.any():
        problems.append(f"duplicate months: {sorted(set(df.loc[dup, 'month'].astype(str)))[:5]}")
    for lo, hi in (("lower_80", "upper_80"), ("lower_95", "upper_95")):
        if lo in df.columns and hi in df.columns:
            l, h = pd.to_numeric(df[lo], errors="coerce"), pd.to_numeric(df[hi], errors="coerce")
            if ((l > h) & l.notna() & h.notna()).any():
                problems.append(f"{lo} greater than {hi} for some months")
            if ((l < 0) & l.notna()).any():
                problems.append(f"{lo} contains negative values")
    if problems:
        raise DataValidationError(label, problems)


def check_month_continuity(months: pd.Series) -> list[str]:
    """Return the list of missing months (YYYY-MM) between the first and last month."""
    m = pd.to_datetime(months.astype(str), format="%Y-%m").sort_values()
    full = pd.period_range(m.iloc[0], m.iloc[-1], freq="M")
    present = set(m.dt.to_period("M"))
    return [str(p) for p in full if p not in present]


def validate_m3_benchmark(df: pd.DataFrame, label: str = "M3 benchmark") -> None:
    required = ["country", "indicator", "unit", "mean_window"]
    require_columns(df, required, label)
    require_numeric(df, ["mean_window"], label)
    problems = []
    for c in ("rank_high_to_low", "n_countries"):
        if c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            if (v < 1).any():
                problems.append(f"{c} must be >= 1")
    if "coverage" in df.columns:
        cov = pd.to_numeric(df["coverage"], errors="coerce")
        if ((cov < 0) | (cov > 1)).any():
            problems.append("coverage must be within [0, 1]")
    if {"rank_high_to_low", "n_countries"} <= set(df.columns):
        r, n = pd.to_numeric(df["rank_high_to_low"]), pd.to_numeric(df["n_countries"])
        if (r > n).any():
            problems.append("rank_high_to_low greater than n_countries")
    dup = df.duplicated(subset=["country", "indicator"], keep=False)
    if dup.any():
        problems.append("duplicate (country, indicator) rows")
    if problems:
        raise DataValidationError(label, problems)


def validate_technical_parameters(df: pd.DataFrame, label: str = "Technology assumptions") -> None:
    """Validate technology-level parameters (also used for the legacy mock technical file)."""
    problems = []
    if "capacity_factor" in df.columns:
        cf = pd.to_numeric(df["capacity_factor"], errors="coerce")
        if ((cf < 0) | (cf > 1)).any():
            problems.append("capacity_factor must be within [0, 1]")
    for c in ("availability_factor", "capacity_credit"):
        if c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            if ((v < 0) | (v > 1)).any():
                problems.append(f"{c} must be within [0, 1]")
    for c in ("capex_musd_per_mw", "fixed_om_musd_per_mw_yr", "variable_cost_usd_per_mwh",
              "emission_factor_t_per_mwh", "max_annual_build_mw", "max_total_new_mw", "cost_per_mwh",
              "co2_t_per_mwh"):
        if c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            if v.isna().any():
                problems.append(f"{c} has missing/non-numeric values")
            elif (v < 0).any():
                problems.append(f"{c} must be non-negative")
    if "lifetime_years" in df.columns:
        v = pd.to_numeric(df["lifetime_years"], errors="coerce")
        if (v <= 0).any() or v.isna().any():
            problems.append("lifetime_years must be positive")
    if problems:
        raise DataValidationError(label, problems)


# Kept for compatibility with the original prototype test-suite name.
validate_m3_technical_mock = validate_technical_parameters


def validate_reference_system(df: pd.DataFrame, label: str = "Reference 2024 system") -> None:
    cols = ["technology", "installed_capacity_mw", "avg_available_capacity_mw", "generation_gwh"]
    require_columns(df, cols, label)
    require_numeric(df, cols[1:], label)
    problems = []
    inst = pd.to_numeric(df["installed_capacity_mw"])
    avail = pd.to_numeric(df["avg_available_capacity_mw"])
    gen = pd.to_numeric(df["generation_gwh"])
    if (inst < 0).any():
        problems.append("installed_capacity_mw must be non-negative")
    if (avail < 0).any():
        problems.append("avg_available_capacity_mw must be non-negative")
    over = avail > inst
    if over.any():
        problems.append(
            "available capacity exceeds installed capacity for: "
            + ", ".join(df.loc[over, "technology"].astype(str))
        )
    if (gen < 0).any():
        problems.append("generation_gwh must be non-negative")
    # physical upper bound: generation cannot exceed installed capacity x 8784 h (leap year 2024)
    impossible = gen * 1000 > inst * 8784 + 1e-6
    if impossible.any():
        problems.append(
            "generation exceeds installed capacity x hours for: "
            + ", ".join(df.loc[impossible, "technology"].astype(str))
        )
    dup = df["technology"].astype(str).str.strip().str.lower().duplicated()
    if dup.any():
        problems.append("duplicate technologies")
    if problems:
        raise DataValidationError(label, problems)
