"""Input adapters: the only place that knows the physical format of M1, M3 and reference data.

    actual file  ->  adapter (load, validate, detect MOCK/REAL, normalize)  ->  M4 internal model

The simulation, optimization engine, agent and API only consume the normalized objects
returned here. When the real M1/M3 outputs arrive, only the manifest (`sources.yml`)
and — if their schema differs — these adapters need to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .load_data import DataLoadError, read_csv, read_yaml
from .prepare_data import normalize_technology, prepare_reference_system, reference_totals
from .prepare_demand import prepare_demand
from .provenance import DataStatus, DatasetProvenance, contains_mock_marker, file_sha256
from .validate_data import (
    DataValidationError,
    check_month_continuity,
    require_columns,
    validate_m1_forecast,
    validate_m3_benchmark,
    validate_reference_system,
    validate_technical_parameters,
)

M1_FULL_SCHEMA = ["month", "actual_gwh", "forecast_gwh", "lower_80", "upper_80", "lower_95",
                  "upper_95", "model", "horizon", "fold", "run_id", "status"]
M3_SCHEMA = ["dataset_version", "model_version", "country", "indicator", "unit", "window_start",
             "window_end", "mean_window", "median_window", "trend_per_year", "trend_pct_per_year",
             "rank_high_to_low", "n_countries", "diff_vs_tun", "distance_to_tun", "n_years", "coverage"]
TUNISIA_LABELS = {"tunisia", "tun", "tunisie"}


@dataclass
class SourceSpec:
    """One entry of the manifest `data/input/sources.yml`."""
    name: str
    path: Path
    declared_status: DataStatus
    dataset_version: str
    producer: str = ""
    description: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, name: str, entry: dict, input_dir: Path) -> "SourceSpec":
        if "path" not in entry:
            raise DataLoadError(f"sources.yml: entry '{name}' has no 'path'")
        raw = str(entry.get("data_status", "UNKNOWN")).upper()
        status = DataStatus(raw) if raw in DataStatus.__members__ else DataStatus.UNKNOWN
        return cls(
            name=name, path=(input_dir / entry["path"]).resolve(), declared_status=status,
            dataset_version=str(entry.get("dataset_version", "unversioned")),
            producer=str(entry.get("producer", "")), description=str(entry.get("description", "")),
            extra={k: v for k, v in entry.items()
                   if k not in {"path", "data_status", "dataset_version", "producer", "description"}},
        )


def _resolve_status(spec: SourceSpec, marker_found: bool, warnings: list[str]) -> DataStatus:
    if spec.declared_status == DataStatus.REAL and marker_found:
        warnings.append("Declared REAL in sources.yml but the file contains mock/synthetic markers: "
                        "treated as MOCK until clarified.")
        return DataStatus.MOCK
    if spec.declared_status == DataStatus.UNKNOWN:
        if marker_found:
            return DataStatus.MOCK
        warnings.append("Data status not declared in sources.yml: reported as UNKNOWN.")
    return spec.declared_status


def _provenance(spec: SourceSpec, df: pd.DataFrame, adapter: str, status: DataStatus,
                transformations: list[str], warnings: list[str], input_dir: Path) -> DatasetProvenance:
    try:
        rel = spec.path.relative_to(input_dir.resolve()).as_posix()
    except ValueError:
        rel = spec.path.name
    return DatasetProvenance(
        source=spec.name, path=rel, data_status=status, dataset_version=spec.dataset_version,
        producer=spec.producer, description=spec.description, sha256=file_sha256(spec.path),
        rows=int(len(df)), adapter=adapter, transformations=transformations, warnings=warnings,
    )


# =====================================================================================
# M1 — demand forecasts
# =====================================================================================
@dataclass
class DemandForecastData:
    monthly: pd.DataFrame          # one row per month, normalized columns
    provenance: DatasetProvenance
    has_intervals: bool
    first_forecast_month: str | None
    last_month: str
    missing_months: list[str]

    def forecast_years(self) -> list[int]:
        f = self.monthly[self.monthly["is_forecast"]]
        counts = f.groupby("year").size()
        return [int(y) for y, n in counts.items() if n == 12]


class M1InputAdapter:
    """Loads the M1 forecast output (full schema or legacy `month,forecast_gwh,...`)."""

    STATUS_PRIORITY = ("final", "production", "forecast", "synthetic_forecast", "backtest", "synthetic_backtest")

    def __init__(self, spec: SourceSpec, input_dir: Path):
        self.spec, self.input_dir = spec, input_dir

    def _select_one_row_per_month(self, df: pd.DataFrame, transformations: list[str]) -> pd.DataFrame:
        if not df["month"].duplicated().any():
            return df
        key_cols = [c for c in ("status", "fold", "horizon", "run_id") if c in df.columns]
        exact = df.duplicated(subset=["month"] + key_cols, keep=False)
        if not key_cols or exact.any():
            months = sorted(set(df.loc[exact if key_cols else df["month"].duplicated(keep=False), "month"]))
            raise DataValidationError("M1 demand forecast", [f"duplicate months: {months[:5]}"])

        def prio(status: str) -> int:
            s = str(status).lower()
            for i, p in enumerate(self.STATUS_PRIORITY):
                if s == p or s.endswith(p):
                    return i
            return len(self.STATUS_PRIORITY)

        tmp = df.copy()
        tmp["_prio"] = tmp["status"].map(prio) if "status" in tmp else 0
        tmp["_h"] = pd.to_numeric(tmp.get("horizon", pd.Series(0, index=tmp.index)).astype(str)
                                  .str.extract(r"(\d+)")[0], errors="coerce").fillna(0)
        tmp = tmp.sort_values(["month", "_prio", "fold" if "fold" in tmp else "_h", "_h"],
                              ascending=[True, True, False, True])
        out = tmp.drop_duplicates("month", keep="first").drop(columns=["_prio", "_h"])
        transformations.append(
            f"Selected one row per month out of {len(df)} rows (priority: status, latest fold, shortest horizon)")
        return out

    def load(self) -> DemandForecastData:
        transformations, warnings = [], []
        df = read_csv(self.spec.path, "M1 demand forecast")
        require_columns(df, ["month", "forecast_gwh"], "M1 demand forecast")
        missing_schema = [c for c in M1_FULL_SCHEMA if c not in df.columns]
        if missing_schema:
            warnings.append("Legacy/partial M1 schema: missing " + ", ".join(missing_schema)
                            + " (filled with empty values).")
            for c in missing_schema:
                df[c] = np.nan
        for c in ("model", "horizon", "fold", "run_id", "status"):
            df[c] = df[c].astype(object).where(df[c].notna(), "").astype(str)
        df["month"] = df["month"].astype(str).str.strip().str[:7]
        df = self._select_one_row_per_month(df, transformations)
        validate_m1_forecast(df)

        prepared = prepare_demand(df, "forecast_gwh")
        transformations.append("average_demand_mw = forecast_gwh x 1000 / (days_in_month x 24)")
        for c in ("actual_gwh", "lower_80", "upper_80", "lower_95", "upper_95"):
            prepared[c] = pd.to_numeric(prepared[c], errors="coerce")
        status_col = prepared["status"].astype(str).str.lower()
        prepared["is_forecast"] = prepared["actual_gwh"].isna() | status_col.str.contains("forecast")
        prepared["month"] = prepared["month"].dt.strftime("%Y-%m")

        has_intervals = bool(prepared["lower_80"].notna().any())
        if not has_intervals:
            warnings.append("No prediction intervals: uncertainty-based demand cases unavailable.")
        missing = check_month_continuity(prepared["month"])
        if missing:
            warnings.append(f"{len(missing)} missing month(s) in M1 series, e.g. {missing[:3]}")

        markers = contains_mock_marker(
            list(prepared["status"].astype(str)) + list(prepared["model"].astype(str))
            + list(prepared["fold"].astype(str)) + list(prepared["run_id"].astype(str)))
        status = _resolve_status(self.spec, markers, warnings)
        if "xgboost" in " ".join(prepared["model"].astype(str).str.lower().unique()) and status == DataStatus.MOCK:
            warnings.append("Model column says XGBoost but data is MOCK: model label is not trusted.")

        cols = ["month", "year", "month_num", "days_in_month", "hours_in_month", "actual_gwh",
                "forecast_gwh", "lower_80", "upper_80", "lower_95", "upper_95", "average_demand_mw",
                "is_forecast", "model", "horizon", "fold", "run_id", "status"]
        monthly = prepared[cols].reset_index(drop=True)
        fc = monthly[monthly["is_forecast"]]
        return DemandForecastData(
            monthly=monthly,
            provenance=_provenance(self.spec, monthly, "M1InputAdapter", status, transformations,
                                   warnings, self.input_dir),
            has_intervals=has_intervals,
            first_forecast_month=str(fc["month"].iloc[0]) if len(fc) else None,
            last_month=str(monthly["month"].iloc[-1]),
            missing_months=missing,
        )


# =====================================================================================
# M3 — international benchmark
# =====================================================================================
@dataclass
class BenchmarkData:
    table: pd.DataFrame
    provenance: DatasetProvenance
    tunisia_label: str | None

    @property
    def indicators(self) -> list[str]:
        return sorted(self.table["indicator"].unique())

    def indicator_summary(self, indicator: str) -> dict:
        t = self.table[self.table["indicator"] == indicator].sort_values("rank_high_to_low")
        if t.empty:
            raise KeyError(indicator)
        peers = t[t["country"] != self.tunisia_label]
        tun = t[t["country"] == self.tunisia_label]
        tun_mean = float(tun["mean_window"].iloc[0]) if len(tun) else None
        return {
            "indicator": indicator,
            "unit": str(t["unit"].iloc[0]),
            "window": f"{int(t['window_start'].iloc[0])}-{int(t['window_end'].iloc[0])}"
            if "window_start" in t and pd.notna(t["window_start"].iloc[0]) else None,
            "tunisia_mean": tun_mean,
            "tunisia_rank": int(tun["rank_high_to_low"].iloc[0]) if len(tun) else None,
            "tunisia_trend_per_year": float(tun["trend_per_year"].iloc[0]) if len(tun) and "trend_per_year" in tun else None,
            "n_countries": int(t["n_countries"].iloc[0]) if "n_countries" in t else len(t),
            "peer_median": float(peers["mean_window"].median()) if len(peers) else None,
            "peer_max": float(peers["mean_window"].max()) if len(peers) else None,
            "peer_min": float(peers["mean_window"].min()) if len(peers) else None,
            "best_country": str(t["country"].iloc[0]),
            "countries": t.replace({np.nan: None}).to_dict(orient="records"),
        }


class M3InputAdapter:
    def __init__(self, spec: SourceSpec, input_dir: Path):
        self.spec, self.input_dir = spec, input_dir

    def load(self) -> BenchmarkData:
        transformations, warnings = [], []
        df = read_csv(self.spec.path, "M3 benchmark")
        missing = [c for c in M3_SCHEMA if c not in df.columns]
        if {"technology", "cost_per_mwh"} <= set(df.columns) and "indicator" not in df.columns:
            raise DataValidationError("M3 benchmark", [
                "this file contains technology cost parameters, not M3 benchmark indicators. "
                "Technology parameters belong in data/input/assumptions/technology_assumptions.csv"])
        validate_m3_benchmark(df)
        if missing:
            warnings.append("M3 schema columns missing: " + ", ".join(missing))
            for c in missing:
                df[c] = np.nan
        df["country"] = df["country"].astype(str).str.strip()
        df["indicator"] = df["indicator"].astype(str).str.strip()
        tun = [c for c in df["country"].unique() if c.lower() in TUNISIA_LABELS]
        tunisia_label = tun[0] if tun else None
        if tunisia_label is None:
            warnings.append("Tunisia not found in M3 benchmark: comparisons vs Tunisia unavailable.")
        low_cov = df[pd.to_numeric(df["coverage"], errors="coerce") < 0.8]
        if len(low_cov):
            warnings.append(f"{len(low_cov)} benchmark rows have coverage < 80 %.")
        markers = contains_mock_marker(list(df["dataset_version"].fillna("").astype(str))
                                       + list(df["model_version"].fillna("").astype(str)))
        status = _resolve_status(self.spec, markers, warnings)
        transformations.append("Trimmed country/indicator labels; no numeric transformation applied")
        return BenchmarkData(
            table=df[M3_SCHEMA].reset_index(drop=True),
            provenance=_provenance(self.spec, df, "M3InputAdapter", status, transformations, warnings, self.input_dir),
            tunisia_label=tunisia_label,
        )


# =====================================================================================
# Reference 2024 system
# =====================================================================================
@dataclass
class ReferenceSystem:
    reference_year: int
    technologies: pd.DataFrame
    totals: dict
    provenance: DatasetProvenance

    def installed(self, tech: str) -> float:
        row = self.technologies[self.technologies["technology"] == tech]
        return float(row["installed_capacity_mw"].iloc[0]) if len(row) else 0.0


class ReferenceSystemAdapter:
    REFERENCE_YEAR = 2024

    def __init__(self, spec: SourceSpec, input_dir: Path):
        self.spec, self.input_dir = spec, input_dir

    def load(self) -> ReferenceSystem:
        warnings: list[str] = []
        df = read_csv(self.spec.path, "Reference 2024 system")
        validate_reference_system(df)
        prepared = prepare_reference_system(df)
        markers = contains_mock_marker(list(df.get("data_status", pd.Series(dtype=str)).astype(str)))
        status = _resolve_status(self.spec, markers, warnings)
        return ReferenceSystem(
            reference_year=self.REFERENCE_YEAR, technologies=prepared, totals=reference_totals(prepared),
            provenance=_provenance(self.spec, prepared, "ReferenceSystemAdapter", status,
                                   ["Normalized technology names", "unavailable = installed - available",
                                    "observed CF = generation / (installed x 8784 h)"], warnings, self.input_dir),
        )


# =====================================================================================
# Technology & system assumptions (separate from M3 by design)
# =====================================================================================
@dataclass
class TechnologyAssumptions:
    technologies: pd.DataFrame
    cf_profiles: dict[str, dict[int, float]]
    retirements: pd.DataFrame
    system: dict[str, dict]
    currency: str
    provenance: DatasetProvenance

    def param(self, name: str) -> float:
        if name not in self.system:
            raise KeyError(f"system parameter '{name}' missing from system_parameters.yml")
        return float(self.system[name]["value"])


class TechnologyAssumptionsAdapter:
    REQUIRED = ["technology", "category", "is_renewable", "capex_musd_per_mw", "fixed_om_musd_per_mw_yr",
                "variable_cost_usd_per_mwh", "emission_factor_t_per_mwh", "lifetime_years",
                "availability_factor", "capacity_credit", "max_annual_build_mw", "max_total_new_mw"]
    CATEGORIES = {"dispatchable", "variable", "energy_limited"}
    SYSTEM_PARAMS = ["discount_rate", "peak_to_average_ratio", "default_reserve_margin", "network_loss_factor",
                     "import_cost_usd_per_mwh", "import_emission_factor_t_per_mwh", "default_max_import_share",
                     "gas_import_fraction", "max_variable_renewable_share_monthly"]

    def __init__(self, spec: SourceSpec, input_dir: Path):
        self.spec, self.input_dir = spec, input_dir

    def load(self) -> TechnologyAssumptions:
        warnings: list[str] = []
        label = "Technology assumptions"
        tech = read_csv(self.spec.path, label)
        require_columns(tech, self.REQUIRED, label)
        validate_technical_parameters(tech, label)
        tech["technology"] = tech["technology"].map(normalize_technology)
        tech["is_renewable"] = tech["is_renewable"].astype(str).str.lower().isin({"true", "1", "yes"})
        bad_cat = set(tech["category"]) - self.CATEGORIES
        if bad_cat:
            raise DataValidationError(label, [f"unknown category {sorted(bad_cat)}; allowed {sorted(self.CATEGORIES)}"])

        prof_path = self.input_dir / self.spec.extra.get("profiles_path", "assumptions/capacity_factor_profiles.csv")
        prof = read_csv(prof_path, "Capacity factor profiles")
        require_columns(prof, ["technology", "month", "capacity_factor"], "Capacity factor profiles")
        validate_technical_parameters(prof[["capacity_factor"]], "Capacity factor profiles")
        prof["technology"] = prof["technology"].map(normalize_technology)
        profiles: dict[str, dict[int, float]] = {}
        for t, g in prof.groupby("technology"):
            months = set(g["month"].astype(int))
            if months != set(range(1, 13)):
                raise DataValidationError("Capacity factor profiles", [f"{t}: profile must define months 1-12"])
            profiles[t] = {int(m): float(v) for m, v in zip(g["month"], g["capacity_factor"])}
        for t, cat in zip(tech["technology"], tech["category"]):
            if cat in {"variable", "energy_limited"} and t not in profiles:
                raise DataValidationError(label, [f"{t} ({cat}) has no monthly capacity factor profile"])

        ret_path = self.input_dir / self.spec.extra.get("retirements_path", "assumptions/capacity_retirements.csv")
        if ret_path.exists():
            ret = pd.read_csv(ret_path)
            require_columns(ret, ["technology", "year", "retired_mw"], "Capacity retirements")
            ret["technology"] = ret["technology"].map(normalize_technology)
            if (pd.to_numeric(ret["retired_mw"]) < 0).any():
                raise DataValidationError("Capacity retirements", ["retired_mw must be non-negative"])
        else:
            ret = pd.DataFrame(columns=["technology", "year", "retired_mw"])
            warnings.append("No retirement schedule: existing capacity assumed constant.")

        sys_path = self.input_dir / self.spec.extra.get("system_parameters_path", "assumptions/system_parameters.yml")
        sys_yaml = read_yaml(sys_path, "System parameters")
        params = sys_yaml.get("parameters", {})
        missing = [p for p in self.SYSTEM_PARAMS if p not in params]
        if missing:
            raise DataValidationError("System parameters", [f"missing parameter(s): {', '.join(missing)}"])

        markers = contains_mock_marker(list(tech.get("data_status", pd.Series(dtype=str)).astype(str))
                                       + [str(sys_yaml.get("data_status", ""))])
        status = _resolve_status(self.spec, markers, warnings)
        return TechnologyAssumptions(
            technologies=tech.reset_index(drop=True), cf_profiles=profiles, retirements=ret, system=params,
            currency=str(sys_yaml.get("currency", "USD")),
            provenance=_provenance(self.spec, tech, "TechnologyAssumptionsAdapter", status,
                                   ["Normalized technology names"], warnings, self.input_dir),
        )
