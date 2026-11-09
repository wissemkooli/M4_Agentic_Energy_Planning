"""Generate the SYNTHETIC / MOCK input datasets used by M4 during independent development.

Outputs (all clearly labelled as synthetic):
  data/input/m1/m1_forecast_synthetic.csv   -> simulates the M1 output schema
  data/input/m3/m3_benchmark_synthetic.csv  -> simulates the M3 output schema

IMPORTANT
- These values were NOT produced by the M1 XGBoost model or by the M3 benchmark pipeline.
- The comparison countries are fictional ("Comparator A" ...). The Tunisia rows of the
  M3 file are synthetic as well and must not be quoted as real statistics.
- The generator is deterministic (fixed seed) so every developer gets identical files.

Usage (from the M4-Mahdi folder):
  python scripts/generate_synthetic_data.py
"""
from __future__ import annotations

import calendar
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
M1_OUT = ROOT / "data" / "input" / "m1" / "m1_forecast_synthetic.csv"
M3_OUT = ROOT / "data" / "input" / "m3" / "m3_benchmark_synthetic.csv"

SEED = 20241
M1_RUN_ID = "synthetic-m1-v1.0"
M1_MODEL = "SYNTHETIC_TREND_SEASONAL"  # deliberately NOT "XGBoost"

# The 2025 values reproduce the original 12-month mock file supplied for M4
# (data/mock/legacy/m1_forecast.csv) so that earlier tests stay comparable.
LEGACY_2025 = [1650, 1480, 1590, 1520, 1680, 1810, 1950, 1920, 1740, 1600, 1550, 1690]

Z80, Z95 = 1.2816, 1.96


def _annual_growth(year: int) -> float:
    """Projection growth rate: 3.2 %/yr in 2026 declining linearly to 2.2 %/yr in 2040."""
    if year <= 2026:
        return 0.032
    return 0.032 - (0.010 * (year - 2026) / (2040 - 2026))


def generate_m1(rng: np.random.Generator) -> pd.DataFrame:
    legacy = np.array(LEGACY_2025, dtype=float)
    shape = legacy / legacy.mean()  # seasonal shape (mean = 1)
    summer = np.array([0, 0, 0, 0, 0.3, 0.7, 1.0, 1.0, 0.6, 0.1, 0, 0])  # cooling months

    rows = []
    # ---------- synthetic history 2019-2024 (actual + back-test forecast) ----------
    hist_growth = {2019: 0.030, 2020: -0.040, 2021: 0.045, 2022: 0.032, 2023: 0.028, 2024: 0.034}
    level_2025 = legacy.mean()
    level = {2025: level_2025}
    for y in range(2024, 2018, -1):
        level[y] = level[y + 1] / (1 + hist_growth.get(y + 1, 0.03))
    for y in range(2019, 2025):
        for m in range(1, 13):
            actual = level[y] * shape[m - 1] * (1 + rng.normal(0, 0.012))
            fc = actual * (1 + rng.normal(0, 0.025))
            sigma = 0.028
            rows.append(dict(
                month=f"{y}-{m:02d}", actual_gwh=round(actual, 1), forecast_gwh=round(fc, 1),
                lower_80=round(fc * (1 - Z80 * sigma), 1), upper_80=round(fc * (1 + Z80 * sigma), 1),
                lower_95=round(fc * (1 - Z95 * sigma), 1), upper_95=round(fc * (1 + Z95 * sigma), 1),
                model=M1_MODEL, horizon="H12", fold=f"synthetic_backtest_{y}", run_id=M1_RUN_ID,
                status="SYNTHETIC_BACKTEST",
            ))

    # ---------- synthetic forecast 2025-2040 ----------
    noise = 0.0
    lvl = level_2025
    h = 0
    for y in range(2025, 2041):
        if y > 2025:
            lvl *= 1 + _annual_growth(y)
        summer_boost = 1 + 0.004 * (y - 2025) * summer  # cooling demand grows faster
        prof = shape * summer_boost
        prof = prof / prof.mean()
        for m in range(1, 13):
            h += 1
            if y == 2025:
                fc = legacy[m - 1]
            else:
                noise = 0.6 * noise + rng.normal(0, 0.006)  # AR(1) perturbation
                fc = lvl * prof[m - 1] * (1 + noise)
            sigma = 0.025 * np.sqrt(h / 12.0)
            rows.append(dict(
                month=f"{y}-{m:02d}", actual_gwh=np.nan, forecast_gwh=round(fc, 1),
                lower_80=round(fc * (1 - Z80 * sigma), 1), upper_80=round(fc * (1 + Z80 * sigma), 1),
                lower_95=round(max(fc * (1 - Z95 * sigma), fc * 0.3), 1),
                upper_95=round(fc * (1 + Z95 * sigma), 1),
                model=M1_MODEL, horizon=f"H{h}", fold="synthetic_projection", run_id=M1_RUN_ID,
                status="SYNTHETIC_FORECAST",
            ))
    df = pd.DataFrame(rows)
    assert (df["forecast_gwh"] > 0).all() and (df["lower_95"] > 0).all()
    return df


# ----------------------------------------------------------------------------------
M3_INDICATORS = {
    # indicator: (unit, tunisia_base, spread, trend_range)
    "renewable_share_electricity": ("%", 4.0, 14.0, (-0.2, 2.2)),
    "gas_share_electricity": ("%", 92.0, 25.0, (-2.0, 0.5)),
    "energy_import_dependence": ("% of primary energy supply", 45.0, 30.0, (-1.5, 1.5)),
    "electricity_generation_per_capita": ("kWh/person", 1700.0, 900.0, (-10.0, 60.0)),
    "co2_intensity_electricity": ("gCO2/kWh", 460.0, 160.0, (-15.0, 2.0)),
    "electricity_network_losses": ("% of supply", 15.0, 6.0, (-0.5, 0.2)),
}
COUNTRIES = ["Tunisia"] + [f"Comparator {c}" for c in "ABCDEFGH"]
WINDOW = (2015, 2023)


def _bounded(indicator: str, x: np.ndarray) -> np.ndarray:
    unit = M3_INDICATORS[indicator][0]
    if unit.startswith("%"):
        return np.clip(x, 0.0, 100.0)
    return np.clip(x, 0.0, None)


def generate_m3(rng: np.random.Generator) -> pd.DataFrame:
    years = np.arange(WINDOW[0], WINDOW[1] + 1)
    records = []
    for ind, (unit, tun_base, spread, (tmin, tmax)) in M3_INDICATORS.items():
        series = {}
        for c in COUNTRIES:
            if c == "Tunisia":
                base = tun_base
                trend = (tmin + tmax) / 4
            else:
                base = tun_base + rng.uniform(-spread, spread)
                trend = rng.uniform(tmin, tmax)
            vals = base + trend * (years - years[0]) + rng.normal(0, spread * 0.03, len(years))
            vals = _bounded(ind, vals)
            mask = np.ones(len(years), dtype=bool)
            if c != "Tunisia" and rng.random() < 0.3:  # some comparators have gaps
                mask[rng.choice(len(years), size=int(rng.integers(1, 3)), replace=False)] = False
            series[c] = (years[mask], vals[mask])

        stats = {}
        for c, (yy, vv) in series.items():
            slope = float(np.polyfit(yy, vv, 1)[0]) if len(yy) >= 2 else np.nan
            mean = float(np.mean(vv))
            stats[c] = dict(mean=mean, median=float(np.median(vv)), slope=slope, n=len(yy))
        order = sorted(COUNTRIES, key=lambda c: -stats[c]["mean"])
        rank = {c: i + 1 for i, c in enumerate(order)}
        tun = stats["Tunisia"]["mean"]
        for c in COUNTRIES:
            s = stats[c]
            diff = s["mean"] - tun
            records.append(dict(
                dataset_version="SYNTHETIC-M3-2026.11",
                model_version="synthetic-benchmark-generator-1.0",
                country=c, indicator=ind, unit=unit,
                window_start=WINDOW[0], window_end=WINDOW[1],
                mean_window=round(s["mean"], 3), median_window=round(s["median"], 3),
                trend_per_year=round(s["slope"], 4),
                trend_pct_per_year=round(100 * s["slope"] / s["mean"], 3) if s["mean"] else np.nan,
                rank_high_to_low=rank[c], n_countries=len(COUNTRIES),
                diff_vs_tun=round(diff, 3), distance_to_tun=round(abs(diff), 3),
                n_years=s["n"], coverage=round(s["n"] / len(years), 3),
            ))
    return pd.DataFrame(records)


def main() -> None:
    rng = np.random.default_rng(SEED)
    m1 = generate_m1(rng)
    m3 = generate_m3(rng)
    M1_OUT.parent.mkdir(parents=True, exist_ok=True)
    M3_OUT.parent.mkdir(parents=True, exist_ok=True)
    m1.to_csv(M1_OUT, index=False)
    m3.to_csv(M3_OUT, index=False)
    print(f"[SYNTHETIC] wrote {len(m1)} M1 rows -> {M1_OUT.relative_to(ROOT)}")
    print(f"[SYNTHETIC] wrote {len(m3)} M3 rows -> {M3_OUT.relative_to(ROOT)}")
    days = sum(calendar.monthrange(2025, m)[1] for m in range(1, 13))
    print(f"2025 synthetic demand: {m1[m1.month.str.startswith('2025')].forecast_gwh.sum():.0f} GWh over {days} days")


if __name__ == "__main__":
    main()
