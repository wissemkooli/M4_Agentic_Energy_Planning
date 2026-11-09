"""Data loading and validation (includes the three original prototype tests)."""
import pandas as pd
import pytest

from m4.data_integration.load_data import DataLoadError, read_csv
from m4.data_integration.prepare_data import prepare_reference_system, reference_totals
from m4.data_integration.prepare_demand import gwh_to_average_mw, hours_in_month, prepare_demand
from m4.data_integration.validate_data import (
    DataValidationError,
    check_month_continuity,
    validate_m1_forecast,
    validate_m3_technical_mock,
    validate_reference_system,
)


# --- original prototype tests ------------------------------------------------------
def test_negative_m1_forecast_is_rejected():
    df = pd.DataFrame({"month": ["2025-01", "2025-02"], "forecast_gwh": [1650, -10]})
    with pytest.raises(DataValidationError, match="strictly positive"):
        validate_m1_forecast(df)


def test_capacity_factor_greater_than_one_is_rejected():
    df = pd.DataFrame({"country": ["A"], "technology": ["solar"], "cost_per_mwh": [35],
                       "co2_t_per_mwh": [0.02], "capacity_factor": [1.2]})
    with pytest.raises(DataValidationError, match="capacity_factor"):
        validate_m3_technical_mock(df)


def test_available_capacity_above_installed_is_rejected():
    df = pd.DataFrame({"technology": ["gas"], "installed_capacity_mw": [100], "avg_available_capacity_mw": [120],
                       "generation_gwh": [100]})
    with pytest.raises(DataValidationError, match="available capacity exceeds"):
        validate_reference_system(df)


# --- additional checks --------------------------------------------------------------
def test_duplicate_months_rejected():
    df = pd.DataFrame({"month": ["2025-01", "2025-01"], "forecast_gwh": [1, 2]})
    with pytest.raises(DataValidationError, match="duplicate months"):
        validate_m1_forecast(df)


def test_invalid_month_format_rejected():
    df = pd.DataFrame({"month": ["2025/01"], "forecast_gwh": [1]})
    with pytest.raises(DataValidationError, match="month format"):
        validate_m1_forecast(df)


def test_interval_bounds_must_be_ordered():
    df = pd.DataFrame({"month": ["2025-01"], "forecast_gwh": [10], "lower_80": [12], "upper_80": [11]})
    with pytest.raises(DataValidationError, match="lower_80 greater than upper_80"):
        validate_m1_forecast(df)


def test_missing_months_detected():
    assert check_month_continuity(pd.Series(["2025-01", "2025-02", "2025-05"])) == ["2025-03", "2025-04"]


def test_missing_required_column():
    with pytest.raises(DataValidationError, match="missing required column"):
        validate_reference_system(pd.DataFrame({"technology": ["gas"]}))


def test_generation_above_physical_maximum_rejected():
    df = pd.DataFrame({"technology": ["solar"], "installed_capacity_mw": [10], "avg_available_capacity_mw": [10],
                       "generation_gwh": [100]})  # 10 MW x 8784 h = 87.8 GWh max
    with pytest.raises(DataValidationError, match="exceeds installed capacity x hours"):
        validate_reference_system(df)


def test_missing_file(tmp_path):
    with pytest.raises(DataLoadError, match="file not found"):
        read_csv(tmp_path / "nope.csv", "X")


def test_reference_preparation_totals(bundle):
    t = bundle.reference.totals
    assert t["total_installed_capacity_mw"] == 4350
    assert t["total_available_capacity_mw"] == 4110
    assert t["total_unavailable_capacity_mw"] == 240
    assert t["total_generation_gwh"] == 17850


def test_technology_normalization():
    df = pd.DataFrame({"technology": ["Natural Gas", "PV"], "installed_capacity_mw": [10, 5],
                       "avg_available_capacity_mw": [9, 5], "generation_gwh": [20, 5]})
    p = prepare_reference_system(df)
    assert set(p["technology"]) == {"gas", "solar"}
    assert reference_totals(p)["total_unavailable_capacity_mw"] == 1


def test_unit_conversion_gwh_to_mw():
    # January: 31 days x 24 h = 744 h ; 1650 GWh -> 1650e3 MWh / 744 h = 2217.7 MW
    assert hours_in_month(2025, 1) == 744
    assert hours_in_month(2024, 2) == 696  # leap year
    assert gwh_to_average_mw(1650, 744) == pytest.approx(2217.74, rel=1e-4)
    df = prepare_demand(pd.DataFrame({"month": ["2025-02", "2025-01"], "forecast_gwh": [1480, 1650]}))
    assert list(df["month_num"]) == [1, 2]
    assert df.loc[1, "average_demand_mw"] == pytest.approx(1480 * 1000 / 672)
