# Mock-to-real data integration guide

M4 was developed **without access to the real M1 and M3 outputs**. It therefore runs on clearly
labelled synthetic inputs that follow the expected schemas. The rest of M4 (simulation, optimization,
agent, API, frontend) only consumes the normalized objects produced by the adapters, so switching to real
data means **replacing files and editing one manifest**, not rewriting the engine.

```
actual file ──► adapter (load · validate · detect MOCK/REAL · normalize) ──► M4 internal model
```

## 1. Current inputs

| Manifest key | File (under `data/input/`) | Status | What it is |
|---|---|---|---|
| `m1_demand_forecast` | `m1/m1_forecast_synthetic.csv` | MOCK | Synthetic monthly demand 2019–2040 in the M1 schema. 2019–2024: synthetic “actuals” + back-test forecasts. 2025–2040: trend × seasonality projection (2025 = original 12-month mock). **Not produced by XGBoost**; model label `SYNTHETIC_TREND_SEASONAL`. |
| `m3_benchmark` | `m3/m3_benchmark_synthetic.csv` | MOCK | Synthetic benchmark in the M3 schema: Tunisia + 8 fictional comparators (“Comparator A–H”), 6 indicators, window 2015–2023. Statistics (mean, median, OLS trend, ranks, diff vs Tunisia, coverage) are computed from synthetic yearly series, so the file is internally consistent. **The Tunisia rows are synthetic too.** |
| `reference_2024` | `reference_2024/reference_capacity_2024.csv` | MOCK | Original fictional 2024 system (4,350 MW installed, 17,850 GWh). |
| `technology_assumptions` | `assumptions/*.csv`, `assumptions/system_parameters.yml` | MOCK | Costs, emission factors, capacity-factor profiles, lifetimes, build limits, retirements, system parameters (including the operational parameters `thermal_unit_size_mw`, `thermal_min_stable_fraction`, `thermal_unit_monthly_fixed_cost_musd`, `operating_reserve_margin`, `value_of_lost_load_usd_per_mwh` used by short-term operational problems). **Separate from M3 by design.** |

Regenerate the synthetic files (deterministic seed): `python scripts/generate_synthetic_data.py`.

Legacy prototype files are kept in `data/mock/legacy/` (see the README there). The old `m3_benchmark.csv`
contained technology costs per fictional country; it is not an M3 output and is no longer read as M3 input
(the M3 adapter rejects such a file with an explicit message).

## 2. The manifest: `data/input/sources.yml`

```yaml
m1_demand_forecast:
  path: m1/m1_forecast_synthetic.csv     # relative to data/input/
  data_status: MOCK                      # MOCK | REAL
  dataset_version: synthetic-m1-v1.0
  producer: "..."
  description: "..."
```

`data_status` is the declaration. The adapters also scan the data for mock markers (`mock`, `synthetic`,
`fictional` in status/model/version columns). If a file declared `REAL` contains such markers it is treated as
MOCK and a warning is shown. If MOCK and REAL inputs are mixed, every result is labelled **MIXED**.

The manifest location can be changed with `DATA_SOURCES_FILE` in `.env`. Files are re-read automatically
when they change (cache keyed on modification times).

## 3. Integrating the real M1 output

1. Copy the real M1 forecast CSV (do **not** move or edit the M1 teammate's original) to
   `data/input/m1/m1_forecast_real.csv`, or point `path` to its location relative to `data/input/`.
2. Edit `sources.yml`:
   ```yaml
   m1_demand_forecast:
     path: m1/m1_forecast_real.csv
     data_status: REAL
     dataset_version: <M1 run_id or release tag>
     producer: "M1 XGBoost pipeline (team repository)"
   ```
3. Check: `python scripts/init_db.py` prints the status of every source; the API `GET /api/data/sources`
   and the frontend page *Data & assumptions* show warnings.

What the `M1InputAdapter` accepts:
- Required columns: `month` (YYYY-MM), `forecast_gwh` (> 0).
- Optional: `actual_gwh, lower_80, upper_80, lower_95, upper_95, model, horizon, fold, run_id, status`
  (missing optional columns are filled with empty values and reported).
- **Several rows per month** (e.g. several folds/horizons of a cross-validation): one row per month is selected
  by status priority (`final` > `production` > `forecast` > `backtest`), then latest fold, then shortest horizon.
  The selection is recorded in the provenance. Identical duplicates are rejected.
- A row is treated as a forecast if `actual_gwh` is empty or `status` contains “forecast”.

If the real horizon is shorter than the planning horizon (e.g. 12 or 24 months), M4 extrapolates the missing
years with a configurable growth rate (default: CAGR of the last 3 complete forecast years; or
`demand.extrapolation_growth_pct` in the scenario). Extrapolated months are labelled `M4_extrapolation` in all
outputs. Alternatively, ask the M1 owner for a longer horizon.

**Adapter changes needed only if:** column names differ (rename map in `M1InputAdapter.load`), the month format
differs (parse in the same place), or status vocabulary differs (`STATUS_PRIORITY`).

## 4. Integrating the real M3 output

1. Copy the real benchmark CSV to `data/input/m3/m3_benchmark_real.csv`.
2. Edit `sources.yml` (`path`, `data_status: REAL`, `dataset_version` = M3 `dataset_version`).

The `M3InputAdapter` requires `country, indicator, unit, mean_window` and uses the full expected schema when
present. Tunisia is recognised as `Tunisia`, `TUN` or `Tunisie` (extend `TUNISIA_LABELS` otherwise). Indicator
names are free: the dashboard and agent list whatever indicators exist. The agent's default indicator choice per
objective is in `m4/agent/graph.py::DEFAULT_BENCHMARKS` — update the names there if M3 uses different ones.

M3 is used for **context only** (comparisons, gaps, trends, evidence for targets). It is never used as a source of
costs, emission factors or capacity factors.

## 5. Replacing the reference system and technical assumptions

- Reference 2024: same schema `technology, installed_capacity_mw, avg_available_capacity_mw, generation_gwh, data_status`.
  Validation rejects available > installed and generation above `installed × 8784 h`.
- Technology assumptions: one row per technology with the columns listed in `TechnologyAssumptionsAdapter.REQUIRED`;
  each `variable`/`energy_limited` technology needs a 12-month profile in `capacity_factor_profiles.csv`.
  Document sources in `source_note` and set `data_status` to `real` when the values come from documented sources.
- A new technology (e.g. biomass) can be added as a `dispatchable` row with a capacity of 0 in the reference file;
  no code change is needed. Storage requires a model extension (see MATHEMATICAL_MODEL.md §9).

## 6. Checklist after switching

- [ ] `python -m pytest` still passes (tests that rely on the specific mock numbers are in `tests/test_data_loading.py::test_reference_preparation_totals` and `tests/test_modeling.py::test_retirements_applied`; adapt them to the new data).
- [ ] `GET /api/data/sources` shows the expected status (REAL / MIXED) and no errors.
- [ ] The 2024 reconciliation gap is understood and documented.
- [ ] Predefined scenario targets (illustrative) are reviewed against real policy targets.
- [ ] Results and strategies no longer show the MOCK banner (only when all inputs are REAL).
