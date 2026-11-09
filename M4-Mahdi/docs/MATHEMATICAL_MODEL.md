# M4 mathematical model

This document describes exactly what is implemented in `m4/modeling/` and `m4/optimization/`.
Every symbol below corresponds to code; file references are given for each block.

## 1. Temporal architecture

| Level | Resolution | Why |
|---|---|---|
| Energy balance, generation, imports | **monthly** (12 per year) | M1 delivers monthly demand; monthly balance captures seasonality of demand (summer cooling peak) and of solar, wind and hydro resources. |
| Investment decisions, installed capacity, retirements | **annual** | Capacity is commissioned per year; planning decisions are annual. |
| Reporting | annual + milestones (start, every 5 years, end) | Strategic reading of results. |

Default horizon: 2025–2040 (configurable, 1 to 36 years, start after the 2024 reference year).

**Limitations of monthly resolution** (explicitly reported in every strategy):
- No hourly/daily variability: intra-day balancing of solar and wind cannot be represented. A proxy limits
  variable renewables to `vre_max` (default 55 %) of monthly supply.
- Peak demand is not in the data: `peak[y] = max_m(average MW in month m) × peak_to_average_ratio` (default 1.20).
- Storage is therefore **not modelled** (a monthly model cannot represent daily charge/discharge cycles
  credibly). The architecture keeps technologies data-driven so storage can be added with representative days
  (charge, discharge, state of charge, round-trip efficiency, power and energy capacity) — see §9.

## 2. Sets and indices

- `t ∈ T` technologies (from `technology_assumptions.csv`; currently gas, solar, wind, hydro)
- `y ∈ Y` planning years, `m ∈ M = {1..12}`
- Subsets: `T_var` (category `variable`: solar, wind), `T_ren` (renewable: solar, wind, hydro), `T_gas` (name contains “gas”)

## 3. Parameters and units (`m4/modeling/system.py`)

| Symbol | Meaning | Unit | Source |
|---|---|---|---|
| `D[y,m]` | demand | GWh | M1 adapter (+ M4 extrapolation, efficiency, multiplier) |
| `R[y,m] = D[y,m]·(1+λ)` | supply requirement, λ = network loss factor (default 0) | GWh | system parameters |
| `h[y,m]` | hours in the month (leap years handled) | h | calendar |
| `E0[t,y]` | existing capacity after scheduled retirements | MW | reference 2024 − retirements |
| `f[t,m]` | output factor: availability (dispatchable) **or** net monthly capacity factor (variable, energy-limited) | – | assumptions |
| `capex_t` | overnight investment cost | MUSD/MW | assumptions |
| `fom_t` | fixed O&M | MUSD/MW-yr | assumptions |
| `vc_t` | variable cost (fuel + variable O&M) | USD/MWh | assumptions |
| `ef_t` | direct emission factor | tCO2/MWh | assumptions |
| `L_t` | lifetime | years | assumptions |
| `cc_t` | capacity credit (firm contribution to peak) | – | assumptions |
| `r` | discount rate | 1/yr | system parameters / scenario |
| `c_imp, ef_imp` | import cost and emission factor | USD/MWh, t/MWh | system parameters |
| `γ` | gas_import_fraction (share of gas generation counted as dependent supply) | – | system parameters |

Unit conversions used throughout:

```
energy [GWh]     = capacity [MW] × factor × hours [h] / 1000
average MW       = GWh × 1000 / hours                       (prepare_demand.py)
cost [MUSD]      = energy [GWh] × price [USD/MWh] / 1000     (1 GWh = 1000 MWh; 1 MUSD = 1e6 USD)
emissions [Mt]   = energy [GWh] × ef [t/MWh] / 1000
```

**Capacity factor ≠ availability.** Availability is the fraction of installed capacity that is technically
available (outages); for gas it is 3300/3500 = 0.943 in the mock reference. A capacity factor is realised
output / (installed × hours). For variable and hydro plants the model uses a *net monthly capacity factor*
(resource × availability), so availability is not applied twice.

## 4. Decision variables (`m4/optimization/model.py`)

| Variable | Domain | Unit | Interpretation |
|---|---|---|---|
| `new[t,y]` | `0 ≤ new ≤ max_annual_build_t` (0 if investment disabled) | MW | capacity commissioned at the start of year y |
| `gen[t,y,m]` | ≥ 0 | GWh | electricity generated **and used** |
| `imp[y,m]` | ≥ 0 (fixed to 0 if imports disabled) | GWh | imported electricity |

Derived: `cap[t,y] = E0[t,y] + Σ_{y' ≤ y, y − y' < L_t} new[t,y']` (MW).
Curtailment is implicit: `curt[t,y,m] = cap·f·h/1000 − gen ≥ 0` for variable technologies (reported, not a variable).

## 5. Constraints

| Name | Equation | In v1 |
|---|---|---|
| Energy balance | `Σ_t gen[t,y,m] + imp[y,m] = R[y,m]` ∀ y,m | ✔ |
| Available output | `gen[t,y,m] ≤ cap[t,y] · f[t,m] · h[y,m] / 1000` | ✔ |
| Variable RE integration (proxy) | `Σ_{t∈T_var} gen[t,y,m] ≤ vre_max · R[y,m]` | ✔ |
| Import limit | `Σ_m imp[y,m] ≤ imp_max · Σ_m R[y,m]` | ✔ |
| Reserve margin | `Σ_t cc_t · cap[t,y] ≥ (1 + ρ) · peak[y]` | ✔ |
| Build limits | `new[t,y] ≤ max_annual_t`, `Σ_y new[t,y] ≤ max_total_t`, optional `Σ_y new ≥ min_total_t` | ✔ |
| Total budget | `Σ_{t,y} capex_t · new[t,y] ≤ B` (overnight, undiscounted MUSD) | ✔ optional |
| Annual budget | `Σ_t capex_t · new[t,y] ≤ B_y` ∀ y | ✔ optional |
| Renewable target | `Σ_{t∈T_ren} Σ_m gen ≥ s_y · Σ_m R[y,m]` for y ≥ target year | ✔ optional |
| Gas share limit | `Σ_{t∈T_gas} Σ_m gen ≤ g_y · Σ_m R[y,m]` for y ≥ target year | ✔ optional |
| CO2 cap | `E[y] = Σ_{t,m} ef_t·gen/1000 + ef_imp·Σ_m imp/1000 ≤ cap_y` | ✔ optional |

Targets hold from their year onwards (a 2030 target also applies 2031–end). Several targets combine as the
strictest active value. An emission *reduction* target `x %` is converted to `cap_y = (1 − x/100) · E_2024`,
where `E_2024` is computed from the 2024 reference generation and the configured emission factors
(7.312 Mt with the mock data). This baseline choice is shown to the user for approval.

**Extensions not in v1:** storage, construction lead times, endogenous retirement, ramping/unit commitment,
network constraints, hourly profiles, demand response.

## 6. Objectives

All objectives are linear expressions of the variables:

| Key | Definition | Unit |
|---|---|---|
| `min_cost` | `Σ_y DF_y · [Σ_t CRF_t·capex_t·(active new capacity) + Σ_t fom_t·cap[t,y] + Σ_{t,m} vc_t·gen/1000 + c_imp·Σ_m imp/1000]` with `DF_y = (1+r)^-(y−2024)`, `CRF = r(1+r)^L/((1+r)^L−1)` | MUSD (NPV 2024) |
| `min_emissions` | `Σ_y E[y]` | MtCO2 |
| `min_dependence` | `Σ_y (γ · Σ_{t∈T_gas,m} gen + Σ_m imp)` | GWh |
| `max_renewables` | `Σ_{t∈T_ren,y,m} gen` | GWh |
| `min_investment` | `Σ_{t,y} capex_t · new[t,y]` | MUSD (overnight) |

Cost accounting avoids double counting: capital enters the objective only through annualised capital
charges for the years an asset is in service **within** the horizon (so assets built late are not charged their
full capex), operating costs are separate, and imports are priced only once. The budget constraint uses
overnight capex because that is what a budget limits.

**Renewable share definition:** `RE share[y] = Σ_{t∈T_ren} gen / Σ_m R[y,m]`. The denominator is total supply
requirement (domestic generation + imports). Hydro counts as renewable; imports are **not** counted as renewable
(origin unknown); curtailed energy is excluded because `gen` is energy actually used. Because `Σ R` is fixed by
demand, maximising renewable generation is equivalent to maximising the renewable share (linear).

**Energy dependence:** `(γ · gas generation + imports) / supply`. With mock `γ = 1.0`, all gas generation counts as
externally dependent (conservative upper bound); replace `γ` with a documented value.

**Emission accounting scope:** domestic production; imported electricity has `ef_imp = 0` by default. Consequence:
the minimum-emission strategy uses imports up to the import limit. This is reported as an assumption.

## 7. Multi-objective methods (`m4/optimization/engine.py`)

| Method | Formulation | Meaning / trade-off |
|---|---|---|
| Single | `min f_k` | One objective; others are reported, not optimised. |
| Weighted sum | Payoff table → utopia `u_k`, nadir `n_k`; `min Σ_k w_k (f_k − u_k)/(n_k − u_k)` | Normalisation makes weights comparable. Finds supported Pareto points only; weights have no physical meaning. |
| ε-constraint | `min f_p s.t. f_k ≤ ε_k` (ε absolute, or fraction between utopia and nadir) | Reaches any Pareto point; bounds are interpretable (e.g. “at most 80 Mt”). |
| Lexicographic | for k in priorities: `min f_k s.t. f_j ≤ (1+τ) f_j*` for earlier j | Strict priority order with tolerance τ (default 1 %). |
| Pareto analysis | ε-constraint sweep of a secondary objective from nadir to utopia | Shows the cost of each additional unit of emission reduction. |

Numerical details: non-cost objectives receive a `1e-6 × NPV` tie-break so the reported optimum is not an
arbitrary point among alternative optima; ε bounds derived from solved values get a `1e-7` relative tolerance.
All reported objective values are **recomputed from the solution** (not taken from the weighted objective).

## 8. Validation and infeasibility

After every optimal solve (`m4/optimization/validation.py`):
1. Physical checks recomputed from the solution: monthly balance residual, output ≤ available, non-negativity, build limits.
2. Objective consistency: Pyomo expressions vs independent recomputation (`m4/modeling/indicators.py`).
3. Constraint satisfaction table (value, limit, margin) for every policy and reliability constraint.
4. Adequacy re-check: the optimised capacity plan is re-simulated with the merit-order simulation; unserved energy must be 0.

If the solver reports infeasibility, no solution is returned. An elastic reformulation
(`m4/optimization/diagnostics.py`) adds non-negative slacks to the policy constraints (unserved energy,
renewable target, gas share, CO2 cap, import share, reserve margin, budgets, minimum builds) and minimises
their weighted sum. Non-zero slacks explain which constraints conflict and by how much. Technical limits
(build limits, VRE proxy) stay hard. If even the elastic model is infeasible the technical limits contradict
each other, and that is reported.

## 9. Simulation engine (`m4/modeling/simulation.py`)

The simulation evaluates a **given** capacity plan without optimising it: monthly merit-order dispatch sorted by
variable cost (imports inserted at their cost), VRE limited by `vre_max·R`, imports drawing on an annual allowance
consumed chronologically, and any remaining requirement reported as **unserved energy**. It is used to
(1) quantify the shortfall of the existing fleet, (2) simulate user-defined expansion plans, and (3) independently
re-check optimised plans. It shares `compute_indicators` with the optimizer, so metrics are identical.

**Adding storage later:** add a `storage` category with variables `charge[s,y,d,h]`, `discharge`, `soc` on
representative days, constraints `soc[h] = soc[h−1] + η_c·charge − discharge/η_d`, `soc ≤ E_cap`,
`charge, discharge ≤ P_cap`, and separate power and energy investment variables. The balance equation becomes
`Σ gen + imp + discharge − charge = R`.

## 10. 2024 reconciliation

Mock 2024 M1 actual demand (19,497 GWh) exceeds mock 2024 reference generation (17,850 GWh) by 8.4 %.
The model **does not force** these to balance: the 2024 reference defines initial installed capacity only, and the
energy balance is enforced from the first planning year. The gap is reported in the data quality report
(possible explanations: imports, losses, demand definition) and must be clarified with the data owners.

## 11. Problem size and performance

For 2025–2040: 64 investment, 768 generation and 192 import variables; about 1,200 constraints (plus policy constraints).
Model build ≈ 0.3 s, HiGHS solve ≈ 0.2–0.6 s; a strategy set (5–8 solves) takes 3–8 s on a laptop.
