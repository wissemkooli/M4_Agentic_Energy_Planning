"""Solver-independent inputs of a short-term operational problem.

Reuses `build_system_inputs` (same technologies, M1 demand pathway, retirements and
system parameters as the planning model) for the years covering the operating period,
then applies the operational settings: demand change, outages, unit sizes, reserve and
fuel limits. Simulation, optimization and validation all consume this object.

Units: MW, GWh (= MW x h / 1000), MUSD, USD/MWh (cost[MUSD] = GWh x USD/MWh / 1000).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..data_integration.repository import InputBundle
from ..modeling.system import ScenarioDataError, SystemInputs, Technology, build_system_inputs
from ..scenarios.schema import ScenarioConfig
from .schema import OperationalProblem, period_label


@dataclass
class OperationalInputs:
    problem: OperationalProblem
    periods: list[tuple[int, int]]
    labels: dict[tuple[int, int], str]
    techs: dict[str, Technology]
    dispatchable: list[str]                       # techs with unit commitment (gas)
    hours: dict[tuple[int, int], float]
    demand_m1_gwh: dict[tuple[int, int], float]   # M1 value of the chosen basis (before the demand change)
    demand_gwh: dict[tuple[int, int], float]      # supply requirement used in the balance
    demand_source: dict[tuple[int, int], str]     # M1_forecast | M4_extrapolation
    peak_mw: dict[tuple[int, int], float]         # peak used for the reserve requirement
    capacity_mw: dict[tuple[str, tuple[int, int]], float]       # installed in service (after outages), non-dispatchable
    installed_mw: dict[tuple[str, tuple[int, int]], float]      # installed before outages
    units_total: dict[tuple[str, tuple[int, int]], int]
    units_available: dict[tuple[str, tuple[int, int]], int]
    unit_size_mw: float
    min_stable: float
    unit_fixed_cost: float                        # MUSD per unit-month
    reserve_margin: float
    voll: float                                   # USD/MWh
    import_enabled: bool
    import_cost: float
    import_ef: float
    max_import_share: float
    max_vre_share: float
    gas_limit_gwh: float | None
    allow_unserved: bool
    unit_commitment: bool
    data_status: str
    provenance: list[dict]
    assumptions: list[str]
    currency: str

    @property
    def gas_techs(self) -> list[str]:
        return [t for t in self.techs if "gas" in t]

    @property
    def renewable_techs(self) -> list[str]:
        return [t for t, x in self.techs.items() if x.is_renewable]

    @property
    def variable_techs(self) -> list[str]:
        return [t for t, x in self.techs.items() if x.category == "variable"]

    def unit_output_gwh(self, t: str, p: tuple[int, int]) -> float:
        """Average energy one unit in service can deliver in period p."""
        return self.unit_size_mw * self.techs[t].availability * self.hours[p] / 1000.0

    def potential_gwh(self, t: str, p: tuple[int, int]) -> float:
        """Energy available from a non-dispatchable technology in period p."""
        return self.capacity_mw[(t, p)] * self.techs[t].max_output_factor(p[1]) * self.hours[p] / 1000.0

    def firm_capacity_mw(self, units: dict[tuple[str, tuple[int, int]], float], p: tuple[int, int]) -> float:
        firm = sum(units[(t, p)] * self.unit_size_mw * self.techs[t].availability for t in self.dispatchable)
        return firm + sum(self.techs[t].capacity_credit * self.capacity_mw[(t, p)]
                          for t in self.techs if t not in self.dispatchable)


def _system(bundle: InputBundle, problem: OperationalProblem, basis: str) -> SystemInputs:
    years = [p[0] for p in problem.periods]
    sc = ScenarioConfig(name=problem.name, start_year=min(years), end_year=max(years),
                        demand={"basis": basis}, operating={"allow_imports": problem.allow_imports})
    return build_system_inputs(bundle, sc)


def build_operational_inputs(bundle: InputBundle, problem: OperationalProblem) -> OperationalInputs:
    a = bundle.assumptions
    sysi = _system(bundle, problem, problem.demand_basis)
    reserve_basis = problem.reserve_demand_basis or problem.demand_basis
    sys_res = sysi if reserve_basis == problem.demand_basis else _system(bundle, problem, reserve_basis)

    known = set(sysi.techs)
    unknown = sorted({o.technology for o in problem.capacity_outages} - known)
    if unknown:
        raise ScenarioDataError(f"Unknown technology in capacity outages: {unknown} (available: {sorted(known)})")
    outages = {o.technology: o.unavailable_mw for o in problem.capacity_outages}

    periods = problem.periods
    change = 1 + problem.demand_change_pct / 100.0
    ratio = a.param("peak_to_average_ratio")
    src = {(int(r["year"]), int(r["month"])): r["source"] for _, r in sysi.demand.iterrows()}
    hours = {p: sysi.hours[p] for p in periods}
    m1 = {p: sysi.supply_req_gwh[p] for p in periods}
    dem = {p: m1[p] * change for p in periods}
    peak = {p: sys_res.supply_req_gwh[p] * change * 1000.0 / hours[p] * ratio for p in periods}

    size = a.param("thermal_unit_size_mw")
    dispatchable = [t for t, x in sysi.techs.items() if x.category == "dispatchable"]
    cap, inst, n_tot, n_av = {}, {}, {}, {}
    notes = []
    for t in sysi.techs:
        for p in periods:
            installed = sysi.existing_mw[(t, p[0])]
            inst[(t, p)] = installed
            out = min(outages.get(t, 0.0), installed)
            if t in dispatchable:
                total = int(round(installed / size))
                down = int(math.ceil(out / size - 1e-9))
                n_tot[(t, p)], n_av[(t, p)] = total, max(0, total - down)
                cap[(t, p)] = n_av[(t, p)] * size
            else:
                cap[(t, p)] = installed - out
    for t, mw in outages.items():
        if t in dispatchable:
            down = int(math.ceil(mw / size - 1e-9))
            notes.append(f"{mw:,.0f} MW of {t} unavailable = {down} unit(s) of {size:,.0f} MW out of service "
                         "(rounded up, conservative).")
        else:
            notes.append(f"{mw:,.0f} MW of {t} unavailable during the period.")

    rm = (problem.operating_reserve_pct / 100.0) if problem.operating_reserve_pct is not None \
        else a.param("operating_reserve_margin")
    voll = problem.value_of_lost_load_usd_per_mwh or a.param("value_of_lost_load_usd_per_mwh")
    max_imp = (problem.max_import_share_pct / 100.0) if problem.max_import_share_pct is not None \
        else a.param("default_max_import_share")
    labels = {p: period_label(p) for p in periods}
    extrap = [labels[p] for p in periods if src[p] != "M1_forecast"]
    min_stable = a.param("thermal_min_stable_fraction")
    fixed = a.param("thermal_unit_monthly_fixed_cost_musd")
    assumptions = [
        f"Operating period {labels[periods[0]]} to {labels[periods[-1]]} ({len(periods)} month(s)); only the existing "
        f"fleet is used (no investment). Existing capacity: 2024 reference minus scheduled retirements "
        f"({bundle.reference.provenance.data_status.value}).",
        f"Monthly demand: M1 '{problem.demand_basis}'"
        + (f" x {change:.3f} (demand change {problem.demand_change_pct:+g} % requested)" if problem.demand_change_pct else "")
        + (f"; months {extrap} are an M4 extrapolation beyond M1" if extrap else "") + ".",
        f"Gas fleet modelled as units of {size:,.0f} MW (integer decisions); a unit in service delivers between "
        f"{min_stable * 100:.0f} % and 100 % of its available output (availability "
        f"{sysi.techs[dispatchable[0]].availability * 100:.1f} %) and costs {fixed:g} MUSD per month in service."
        if dispatchable and problem.unit_commitment else
        "Gas capacity treated as continuous (LP relaxation, no unit commitment).",
        f"Operating reserve: firm capacity in service >= (1 + {rm * 100:.1f} %) x monthly peak, peak = average MW x "
        f"{ratio:.2f} using M1 '{reserve_basis}'"
        + (f" x {change:.3f}" if problem.demand_change_pct else "") + ".",
        (f"Imports allowed up to {max_imp * 100:.1f} % of monthly demand at {sysi.import_cost:.1f} USD/MWh."
         if problem.allow_imports else "Imports disabled."),
        f"Variable renewables limited to {sysi.max_vre_share * 100:.0f} % of monthly demand (flexibility proxy).",
        (f"Unserved energy allowed, valued at {voll:,.0f} USD/MWh (value of lost load) so it is used only as a last resort."
         if problem.allow_unserved_energy else "Unserved energy not allowed (demand must be fully met)."),
        "Monthly resolution: hourly peaks, start-ups and ramping are approximated by the reserve and minimum-output rules.",
    ] + notes
    if problem.gas_generation_limit_gwh is not None:
        assumptions.append(f"Gas-fired generation limited to {problem.gas_generation_limit_gwh:,.0f} GWh over the period "
                           "(fuel availability).")
    return OperationalInputs(
        problem=problem, periods=periods, labels=labels, techs=sysi.techs, dispatchable=dispatchable, hours=hours,
        demand_m1_gwh=m1, demand_gwh=dem, demand_source={p: src[p] for p in periods}, peak_mw=peak,
        capacity_mw=cap, installed_mw=inst, units_total=n_tot, units_available=n_av, unit_size_mw=size,
        min_stable=min_stable, unit_fixed_cost=fixed, reserve_margin=rm, voll=voll,
        import_enabled=problem.allow_imports, import_cost=sysi.import_cost, import_ef=sysi.import_ef,
        max_import_share=max_imp, max_vre_share=sysi.max_vre_share, gas_limit_gwh=problem.gas_generation_limit_gwh,
        allow_unserved=problem.allow_unserved_energy, unit_commitment=problem.unit_commitment,
        data_status=sysi.data_status, provenance=sysi.provenance, assumptions=assumptions, currency=sysi.currency,
    )
