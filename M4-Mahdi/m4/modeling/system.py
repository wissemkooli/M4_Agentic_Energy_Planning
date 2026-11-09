"""Solver-independent description of the energy system for one scenario.

`build_system_inputs` combines the normalized inputs (M1 demand, 2024 reference system,
technology assumptions) with a scenario and produces plain Python data that both the
merit-order simulation and the Pyomo optimization model consume. Keeping this layer
separate guarantees that simulation and optimization use identical assumptions.

Units
- capacity: MW; energy: GWh; hours: h  ->  energy[GWh] = MW x h / 1000
- investment: MUSD (overnight); fixed O&M: MUSD/MW-yr; variable cost: USD/MWh
  -> cost[MUSD] = GWh x USD/MWh / 1000
- emissions: tCO2/MWh x GWh / 1000 = MtCO2
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..data_integration.provenance import aggregate_status
from ..data_integration.repository import InputBundle
from ..scenarios.schema import REFERENCE_YEAR, ScenarioConfig
from .demand import build_demand_pathway

MONTHS = list(range(1, 13))


class ScenarioDataError(ValueError):
    """Scenario is inconsistent with the available data (e.g. unknown technology)."""


@dataclass
class Technology:
    name: str
    category: str               # dispatchable | variable | energy_limited
    is_renewable: bool
    capex: float                # MUSD/MW (overnight)
    fixed_om: float             # MUSD/MW-yr
    variable_cost: float        # USD/MWh (fuel + variable O&M)
    emission_factor: float      # tCO2/MWh (direct)
    lifetime: int               # years
    availability: float         # fraction (dispatchable units)
    capacity_credit: float      # fraction counted as firm capacity
    max_annual_build: float     # MW/yr
    max_total_new: float        # MW over horizon
    min_total_new: float = 0.0
    allow_investment: bool = True
    cf_profile: dict[int, float] = field(default_factory=dict)  # month -> net capacity factor

    def max_output_factor(self, month: int) -> float:
        """Fraction of installed capacity usable on average in `month`."""
        if self.category == "dispatchable":
            return self.availability
        return self.cf_profile[month]


@dataclass
class SystemInputs:
    scenario: ScenarioConfig
    years: list[int]
    months: list[int]
    techs: dict[str, Technology]
    demand: pd.DataFrame                       # year, month, demand_gwh, hours, source ...
    demand_meta: dict
    hours: dict[tuple[int, int], float]
    demand_gwh: dict[tuple[int, int], float]
    supply_req_gwh: dict[tuple[int, int], float]
    peak_mw: dict[int, float]
    existing_mw: dict[tuple[str, int], float]
    reference_capacity_mw: dict[str, float]
    discount_rate: float
    base_year: int
    import_enabled: bool
    import_cost: float
    import_ef: float
    max_import_share: float
    gas_import_fraction: float
    max_vre_share: float
    reserve_margin: float
    loss_factor: float
    budget_total: float | None
    annual_budget: float | None
    re_min_share: dict[int, float]             # year -> fraction
    gas_max_share: dict[int, float]            # year -> fraction
    emission_cap_mt: dict[int, float]          # year -> MtCO2
    baseline_emissions_mt: float
    baseline_metrics: dict
    currency: str
    provenance: list[dict]
    data_status: str
    assumptions: list[str]

    # helpers --------------------------------------------------------------
    def discount_factor(self, year: int) -> float:
        return 1.0 / (1.0 + self.discount_rate) ** (year - self.base_year)

    def crf(self, tech: Technology) -> float:
        r, n = self.discount_rate, tech.lifetime
        return 1.0 / n if r == 0 else r * (1 + r) ** n / ((1 + r) ** n - 1)

    def annual_supply(self, year: int) -> float:
        return sum(self.supply_req_gwh[(year, m)] for m in self.months)

    def potential_gwh(self, tech: str, year: int, month: int, capacity_mw: float) -> float:
        return capacity_mw * self.techs[tech].max_output_factor(month) * self.hours[(year, month)] / 1000.0

    @property
    def variable_techs(self) -> list[str]:
        return [t for t, x in self.techs.items() if x.category == "variable"]

    @property
    def renewable_techs(self) -> list[str]:
        return [t for t, x in self.techs.items() if x.is_renewable]

    @property
    def gas_techs(self) -> list[str]:
        return [t for t in self.techs if "gas" in t]


def _step_targets(items, years, transform, mode: str) -> dict[int, float]:
    """Targets hold from their year onwards. mode=max (minimum share) or min (caps)."""
    out: dict[int, float] = {}
    for y in years:
        active = [transform(i.value) for i in items if i.year <= y]
        if active:
            out[y] = max(active) if mode == "max" else min(active)
    return out


def build_system_inputs(bundle: InputBundle, scenario: ScenarioConfig) -> SystemInputs:
    a = bundle.assumptions
    sc = scenario
    years = list(range(sc.start_year, sc.end_year + 1))
    demand_df, demand_meta = build_demand_pathway(bundle.demand, sc)

    ref = bundle.reference.technologies.set_index("technology")
    limits = {l.technology: l for l in sc.constraints.technology_limits}
    known = set(a.technologies["technology"])
    unknown = sorted(set(limits) - known) + sorted(set(sc.operating.capex_multipliers) - known)
    if unknown:
        raise ScenarioDataError(f"Unknown technology in scenario: {', '.join(unknown)} (available: {sorted(known)})")
    missing_assumptions = sorted(set(ref.index) - known)
    if missing_assumptions:
        raise ScenarioDataError(f"No technology assumptions for reference technologies: {missing_assumptions}")

    re_mult = sc.operating.renewable_availability_multiplier
    techs: dict[str, Technology] = {}
    for _, r in a.technologies.iterrows():
        name = r["technology"]
        lim = limits.get(name)
        vc = float(r["variable_cost_usd_per_mwh"])
        if "gas" in name:
            vc *= sc.operating.gas_cost_multiplier
        profile = {}
        if r["category"] != "dispatchable":
            profile = {mo: min(1.0, cf * (re_mult if r["is_renewable"] else 1.0))
                       for mo, cf in a.cf_profiles[name].items()}
        t = Technology(
            name=name, category=r["category"], is_renewable=bool(r["is_renewable"]),
            capex=float(r["capex_musd_per_mw"]) * sc.operating.capex_multipliers.get(name, 1.0),
            fixed_om=float(r["fixed_om_musd_per_mw_yr"]), variable_cost=vc,
            emission_factor=float(r["emission_factor_t_per_mwh"]), lifetime=int(r["lifetime_years"]),
            availability=float(r["availability_factor"]), capacity_credit=float(r["capacity_credit"]),
            max_annual_build=float(r["max_annual_build_mw"]), max_total_new=float(r["max_total_new_mw"]),
            cf_profile=profile,
        )
        if lim:
            t.allow_investment = lim.allow_investment
            if lim.max_annual_build_mw is not None:
                t.max_annual_build = lim.max_annual_build_mw
            if lim.max_total_new_mw is not None:
                t.max_total_new = lim.max_total_new_mw
            if lim.min_total_new_mw is not None:
                t.min_total_new = lim.min_total_new_mw
        techs[name] = t

    # existing capacity after exogenous retirements
    ret = a.retirements
    existing = {}
    ref_cap = {t: float(ref.loc[t, "installed_capacity_mw"]) if t in ref.index else 0.0 for t in techs}
    for t in techs:
        for y in years:
            retired = float(ret[(ret["technology"] == t) & (ret["year"] <= y)]["retired_mw"].sum()) if len(ret) else 0.0
            existing[(t, y)] = max(0.0, ref_cap[t] - retired)

    loss = a.param("network_loss_factor")
    hours, dem, req = {}, {}, {}
    for _, r in demand_df.iterrows():
        k = (int(r["year"]), int(r["month"]))
        hours[k] = float(r["hours"])
        dem[k] = float(r["demand_gwh"])
        req[k] = float(r["demand_gwh"]) * (1 + loss)
    ratio = a.param("peak_to_average_ratio")
    peak = {y: max(req[(y, m)] * 1000 / hours[(y, m)] for m in MONTHS) * ratio for y in years}

    c = sc.constraints
    rm = (c.reserve_margin_pct / 100.0) if c.reserve_margin_pct is not None else a.param("default_reserve_margin")
    max_imp = (c.max_import_share_pct / 100.0) if c.max_import_share_pct is not None else a.param("default_max_import_share")
    max_vre = (c.max_vre_share_monthly_pct / 100.0) if c.max_vre_share_monthly_pct is not None \
        else a.param("max_variable_renewable_share_monthly")

    # 2024 reference emissions (baseline of reduction targets) from reference generation
    base_em = 0.0
    for t, row in ref.iterrows():
        if t in techs:
            base_em += float(row["generation_gwh"]) * techs[t].emission_factor / 1000.0
    ref_gen_total = float(ref["generation_gwh"].sum())
    re_ref = sum(float(ref.loc[t, "generation_gwh"]) for t in ref.index if t in techs and techs[t].is_renewable)
    gas_ref = sum(float(ref.loc[t, "generation_gwh"]) for t in ref.index if "gas" in t)
    baseline_metrics = {
        "reference_year": REFERENCE_YEAR,
        "emissions_mt": round(base_em, 4),
        "generation_gwh": ref_gen_total,
        "renewable_share_of_generation": round(re_ref / ref_gen_total, 4) if ref_gen_total else None,
        "gas_share_of_generation": round(gas_ref / ref_gen_total, 4) if ref_gen_total else None,
        "note": "Computed from the 2024 reference generation and the configured emission factors.",
    }

    caps = _step_targets(c.emission_caps_mt, years, lambda v: v, "min")
    red = _step_targets(c.emission_reduction_targets, years, lambda v: base_em * (1 - v / 100.0), "min")
    emission_cap = {y: min(v for v in (caps.get(y), red.get(y)) if v is not None)
                    for y in years if y in caps or y in red}

    disc = sc.operating.discount_rate if sc.operating.discount_rate is not None else a.param("discount_rate")
    assumptions = [
        f"Reference year {REFERENCE_YEAR}: existing capacities from the reference system ({bundle.reference.provenance.data_status.value}).",
        f"Demand basis: M1 '{demand_meta['basis']}'"
        + (f"; years {demand_meta['extrapolated_years'][0]}-{demand_meta['extrapolated_years'][-1]} extrapolated by M4 at "
           f"{demand_meta['extrapolation_growth_pct']:.2f} %/yr" if demand_meta["extrapolated_years"] else "")
        + ".",
        f"Peak demand = max monthly average MW x {ratio:.2f} (monthly data proxy).",
        f"Reserve margin {rm * 100:.1f} % on firm capacity (capacity credit x installed).",
        f"Imports {'allowed up to ' + format(max_imp * 100, '.1f') + ' % of annual supply' if sc.operating.allow_imports else 'disabled'}"
        f" at {a.param('import_cost_usd_per_mwh'):.1f} USD/MWh; import emissions factor {a.param('import_emission_factor_t_per_mwh')} t/MWh.",
        f"Variable renewables limited to {max_vre * 100:.0f} % of monthly supply (flexibility proxy; no storage modelled).",
        f"Discount rate {disc * 100:.1f} %; investments annualized with capital recovery factors; NPV in {a.currency}.",
        f"Network loss factor {loss:.3f} applied to demand.",
        "New capacity is available from the start of its commissioning year (no construction lead time).",
        f"Renewable availability multiplier {re_mult:.2f}; gas cost multiplier {sc.operating.gas_cost_multiplier:.2f}.",
    ]
    return SystemInputs(
        scenario=sc, years=years, months=MONTHS, techs=techs, demand=demand_df, demand_meta=demand_meta,
        hours=hours, demand_gwh=dem, supply_req_gwh=req, peak_mw=peak, existing_mw=existing,
        reference_capacity_mw=ref_cap, discount_rate=disc, base_year=REFERENCE_YEAR,
        import_enabled=sc.operating.allow_imports, import_cost=a.param("import_cost_usd_per_mwh"),
        import_ef=a.param("import_emission_factor_t_per_mwh"), max_import_share=max_imp,
        gas_import_fraction=a.param("gas_import_fraction"), max_vre_share=max_vre, reserve_margin=rm,
        loss_factor=loss, budget_total=c.budget_total_musd, annual_budget=c.annual_budget_musd,
        re_min_share=_step_targets(c.renewable_share_targets, years, lambda v: v / 100.0, "max"),
        gas_max_share=_step_targets(c.max_gas_share, years, lambda v: v / 100.0, "min"),
        emission_cap_mt=emission_cap, baseline_emissions_mt=base_em, baseline_metrics=baseline_metrics,
        currency=a.currency, provenance=bundle.provenance_dicts(), data_status=bundle.overall_status.value,
        assumptions=assumptions,
    )
