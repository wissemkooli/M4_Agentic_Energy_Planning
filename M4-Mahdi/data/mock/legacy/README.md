# Legacy mock files (MOCK — fictional values)

These are the original prototype mock inputs of M4, kept for traceability.

| File | Original location | Status |
|------|-------------------|--------|
| `m1_forecast.csv` | `data/input/m1/m1_forecast.csv` | 12 fictional monthly forecasts for 2025. The 2025 rows of the synthetic M1 dataset reproduce these values exactly. Still loadable by `M1InputAdapter` (legacy schema is supported). |
| `m3_benchmark_technical_mock.csv` | `data/input/m3/m3_benchmark.csv` | Fictional **technical parameters** (cost, CO2 factor, capacity factor) per fictional country. This is **not** an M3 benchmark output: real M3 provides indicator statistics, not technology parameters. It is therefore no longer read as M3 input. Its gas emission factor and gas variable cost were used as the basis of `data/input/assumptions/technology_assumptions.csv` (see the `source_note` column). |

None of these values are official Tunisian statistics.
