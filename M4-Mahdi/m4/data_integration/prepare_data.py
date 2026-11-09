"""Reference system preparation (functionality of the original M4 prototype).

- normalizes technology names,
- computes unavailable capacity per technology,
- computes system totals (installed, available, unavailable, generation),
- derives the observed 2024 capacity factor and availability ratio per technology.
"""
from __future__ import annotations

import pandas as pd

TECHNOLOGY_ALIASES = {
    "natural gas": "gas", "natural_gas": "gas", "ccgt": "gas", "gas turbine": "gas",
    "pv": "solar", "solar pv": "solar", "photovoltaic": "solar",
    "onshore wind": "wind", "wind onshore": "wind",
    "hydropower": "hydro", "hydroelectric": "hydro",
}

HOURS_2024 = 366 * 24  # 2024 is a leap year


def normalize_technology(name: str) -> str:
    key = str(name).strip().lower()
    return TECHNOLOGY_ALIASES.get(key, key.replace(" ", "_"))


def prepare_reference_system(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["technology"] = out["technology"].map(normalize_technology)
    for c in ("installed_capacity_mw", "avg_available_capacity_mw", "generation_gwh"):
        out[c] = pd.to_numeric(out[c])
    out["unavailable_capacity_mw"] = out["installed_capacity_mw"] - out["avg_available_capacity_mw"]
    out["availability_ratio"] = (out["avg_available_capacity_mw"] / out["installed_capacity_mw"]).where(
        out["installed_capacity_mw"] > 0
    )
    out["observed_capacity_factor"] = (
        out["generation_gwh"] * 1000 / (out["installed_capacity_mw"] * HOURS_2024)
    ).where(out["installed_capacity_mw"] > 0)
    return out.sort_values("technology").reset_index(drop=True)


def reference_totals(prepared: pd.DataFrame) -> dict:
    return {
        "total_installed_capacity_mw": float(prepared["installed_capacity_mw"].sum()),
        "total_available_capacity_mw": float(prepared["avg_available_capacity_mw"].sum()),
        "total_unavailable_capacity_mw": float(prepared["unavailable_capacity_mw"].sum()),
        "total_generation_gwh": float(prepared["generation_gwh"].sum()),
    }
