"""Raw file loading (first stage of the data pipeline).

These functions only read files; they do not validate or transform. Validation lives
in `validate_data.py`, normalization in the adapters. Kept compatible with the original
M4 prototype functions (`load_m1_forecast`, `load_m3_benchmark`, `load_reference_system`).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml


class DataLoadError(Exception):
    """Raised when an input file is missing or cannot be parsed."""


def read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise DataLoadError(f"{label}: file not found at '{path}'. Check data/input/sources.yml.")
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # pragma: no cover - depends on file content
        raise DataLoadError(f"{label}: cannot parse CSV '{path.name}': {exc}") from exc
    if df.empty:
        raise DataLoadError(f"{label}: file '{path.name}' contains no rows.")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def read_yaml(path: Path, label: str) -> dict:
    if not path.exists():
        raise DataLoadError(f"{label}: file not found at '{path}'.")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise DataLoadError(f"{label}: '{path.name}' must contain a mapping.")
    return data


# Backwards-compatible helpers from the original prototype -----------------------------
def load_m1_forecast(path: Path) -> pd.DataFrame:
    return read_csv(Path(path), "M1 demand forecast")


def load_m3_benchmark(path: Path) -> pd.DataFrame:
    return read_csv(Path(path), "M3 benchmark")


def load_reference_system(path: Path) -> pd.DataFrame:
    return read_csv(Path(path), "Reference 2024 system")
