"""Input adapters: schema handling, MOCK/REAL detection, normalization, error messages."""
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml

from m4.data_integration.adapters import M1InputAdapter, M3InputAdapter, SourceSpec
from m4.data_integration.load_data import DataLoadError
from m4.data_integration.provenance import DataStatus
from m4.data_integration.quality import data_quality_report
from m4.data_integration.repository import load_inputs
from m4.data_integration.validate_data import DataValidationError

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "data" / "input"


def _spec(path: Path, status="MOCK", **extra):
    return SourceSpec(name="t", path=path, declared_status=DataStatus(status), dataset_version="v", extra=extra)


def test_bundle_is_mock_and_complete(bundle):
    assert bundle.overall_status == DataStatus.MOCK
    assert all(p.data_status == DataStatus.MOCK for p in bundle.provenance)
    assert bundle.demand.forecast_years()[0] == 2025 and bundle.demand.forecast_years()[-1] == 2040
    assert bundle.demand.has_intervals
    assert bundle.benchmark.tunisia_label == "Tunisia"
    assert all(len(p.sha256) == 64 for p in bundle.provenance)


def test_synthetic_m1_not_labelled_xgboost(bundle):
    assert not bundle.demand.monthly["model"].str.contains("XGBoost", case=False).any()


def test_legacy_m1_schema_supported():
    d = M1InputAdapter(_spec(ROOT / "data/mock/legacy/m1_forecast.csv"), ROOT / "data").load()
    assert len(d.monthly) == 12 and not d.has_intervals
    assert any("Legacy/partial M1 schema" in w for w in d.provenance.warnings)
    assert d.forecast_years() == [2025]


def test_real_declared_but_mock_markers(tmp_path):
    p = tmp_path / "m1.csv"
    pd.DataFrame({"month": ["2025-01"], "forecast_gwh": [100], "status": ["mock"]}).to_csv(p, index=False)
    d = M1InputAdapter(_spec(p, "REAL"), tmp_path).load()
    assert d.provenance.data_status == DataStatus.MOCK
    assert any("Declared REAL" in w for w in d.provenance.warnings)


def test_real_data_is_reported_real(tmp_path):
    p = tmp_path / "m1.csv"
    pd.DataFrame({"month": ["2025-01", "2025-02"], "forecast_gwh": [100, 90], "model": ["XGBoost"] * 2,
                  "status": ["final"] * 2}).to_csv(p, index=False)
    d = M1InputAdapter(_spec(p, "REAL"), tmp_path).load()
    assert d.provenance.data_status == DataStatus.REAL


def test_m1_multiple_folds_selects_one_row_per_month(tmp_path):
    p = tmp_path / "m1.csv"
    pd.DataFrame({"month": ["2025-01", "2025-01", "2025-02"], "forecast_gwh": [100, 105, 90],
                  "fold": ["fold_1", "fold_2", "fold_2"], "horizon": ["H1", "H1", "H2"],
                  "status": ["backtest", "forecast", "forecast"]}).to_csv(p, index=False)
    d = M1InputAdapter(_spec(p, "REAL"), tmp_path).load()
    assert len(d.monthly) == 2
    assert d.monthly.loc[0, "forecast_gwh"] == 105
    assert any("Selected one row per month" in t for t in d.provenance.transformations)


def test_m1_negative_demand_rejected(tmp_path):
    p = tmp_path / "m1.csv"
    pd.DataFrame({"month": ["2025-01"], "forecast_gwh": [-1]}).to_csv(p, index=False)
    with pytest.raises(DataValidationError):
        M1InputAdapter(_spec(p), tmp_path).load()


def test_m1_missing_months_warned(tmp_path):
    p = tmp_path / "m1.csv"
    pd.DataFrame({"month": ["2025-01", "2025-03"], "forecast_gwh": [1, 2]}).to_csv(p, index=False)
    d = M1InputAdapter(_spec(p), tmp_path).load()
    assert d.missing_months == ["2025-02"]


def test_m3_rejects_technical_parameter_file():
    with pytest.raises(DataValidationError, match="technology cost parameters"):
        M3InputAdapter(_spec(ROOT / "data/mock/legacy/m3_benchmark_technical_mock.csv"), ROOT).load()


def test_m3_ranks_consistent(bundle):
    t = bundle.benchmark.table
    for ind, g in t.groupby("indicator"):
        assert sorted(g["rank_high_to_low"]) == list(range(1, len(g) + 1))
        ordered = g.sort_values("rank_high_to_low")["mean_window"].tolist()
        assert ordered == sorted(ordered, reverse=True)
        tun = g[g["country"] == "Tunisia"]["mean_window"].iloc[0]
        assert (g["diff_vs_tun"] - (g["mean_window"] - tun)).abs().max() < 0.01


def _copy_inputs(tmp_path):
    dst = tmp_path / "input"
    shutil.copytree(INPUT, dst)
    return dst


def test_missing_technology_assumption_reported(tmp_path):
    dst = _copy_inputs(tmp_path)
    ta = dst / "assumptions/technology_assumptions.csv"
    df = pd.read_csv(ta)
    df[df["technology"] != "wind"].to_csv(ta, index=False)
    prof = dst / "assumptions/capacity_factor_profiles.csv"
    p = pd.read_csv(prof)
    p[p["technology"] != "wind"].to_csv(prof, index=False)
    b = load_inputs(dst / "sources.yml")
    rep = data_quality_report(b)
    assert any(i["level"] == "error" and "wind" in i["message"] for i in rep["issues"])


def test_manifest_missing_entry(tmp_path):
    dst = _copy_inputs(tmp_path)
    data = yaml.safe_load((dst / "sources.yml").read_text())
    data.pop("m3_benchmark")
    (dst / "sources.yml").write_text(yaml.safe_dump(data))
    with pytest.raises(DataLoadError, match="m3_benchmark"):
        load_inputs(dst / "sources.yml")


def test_quality_report_flags_2024_gap(bundle):
    rep = data_quality_report(bundle)
    rec = rep["reconciliation_2024"]
    assert rec and rec["gap_gwh"] > 0 and "Not forced" in rec["treatment"]
