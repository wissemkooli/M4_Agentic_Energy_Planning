"""Energy, economic and environmental indicators computed from a dispatch.

The same function evaluates simulation outputs and optimization outputs, so both are
always measured with identical definitions:

- supply S[y]          = sum_m supply requirement (demand x (1 + losses))       [GWh]
- renewable share      = renewable generation used / S  (hydro counts as renewable,
                         curtailed energy and imports are excluded from the numerator)
- gas share            = gas generation / S
- import share         = imports / S
- dependence share     = (gas_import_fraction x gas generation + imports) / S
- emissions E[y]       = sum_t ef_t x gen_t / 1000 + ef_imp x imports / 1000     [MtCO2]
- system cost (NPV)    = sum_y DF_y x (annualized capital + fixed O&M + variable cost
                         + import cost)                                          [MUSD]
- overnight investment = sum capex x new capacity (used for budget constraints)  [MUSD]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .system import SystemInputs


@dataclass
class Dispatch:
    new_mw: dict[tuple[str, int], float]
    gen_gwh: dict[tuple[str, int, int], float]
    imp_gwh: dict[tuple[int, int], float]
    unserved_gwh: dict[tuple[int, int], float] = field(default_factory=dict)


def capacity_by_year(inputs: SystemInputs, new_mw: dict[tuple[str, int], float]) -> dict[tuple[str, int], float]:
    cap = {}
    for t, tech in inputs.techs.items():
        for y in inputs.years:
            built = sum(new_mw.get((t, yy), 0.0) for yy in inputs.years if yy <= y and y - yy < tech.lifetime)
            cap[(t, y)] = inputs.existing_mw[(t, y)] + built
    return cap


def _r(x: float, n: int = 3) -> float:
    return round(float(x), n)


def compute_indicators(inputs: SystemInputs, d: Dispatch) -> dict:
    T, Y, M = list(inputs.techs), inputs.years, inputs.months
    cap = capacity_by_year(inputs, d.new_mw)
    annual, monthly = [], []
    tot = dict(cost=0.0, inv=0.0, em=0.0, re=0.0, supply=0.0, gas=0.0, imp=0.0, dep=0.0, uns=0.0, curt=0.0)
    for y in Y:
        gen_t = {t: sum(d.gen_gwh.get((t, y, m), 0.0) for m in M) for t in T}
        imp = sum(d.imp_gwh.get((y, m), 0.0) for m in M)
        uns = sum(d.unserved_gwh.get((y, m), 0.0) for m in M)
        supply = inputs.annual_supply(y)
        demand = sum(inputs.demand_gwh[(y, m)] for m in M)
        curt_t = {}
        for t in T:
            if inputs.techs[t].category == "variable":
                pot = sum(inputs.potential_gwh(t, y, m, cap[(t, y)]) for m in M)
                curt_t[t] = max(0.0, pot - gen_t[t])
        re = sum(gen_t[t] for t in inputs.renewable_techs)
        gas = sum(gen_t[t] for t in inputs.gas_techs)
        dep = inputs.gas_import_fraction * gas + imp
        em = sum(inputs.techs[t].emission_factor * gen_t[t] for t in T) / 1000 + inputs.import_ef * imp / 1000
        inv = sum(inputs.techs[t].capex * d.new_mw.get((t, y), 0.0) for t in T)
        ann_cap = sum(
            inputs.crf(inputs.techs[t]) * inputs.techs[t].capex
            * sum(d.new_mw.get((t, yy), 0.0) for yy in Y if yy <= y and y - yy < inputs.techs[t].lifetime)
            for t in T)
        fom = sum(inputs.techs[t].fixed_om * cap[(t, y)] for t in T)
        vom = sum(inputs.techs[t].variable_cost * gen_t[t] for t in T) / 1000
        impc = inputs.import_cost * imp / 1000
        total = ann_cap + fom + vom + impc
        df = inputs.discount_factor(y)
        firm = sum(inputs.techs[t].capacity_credit * cap[(t, y)] for t in T)
        peak = inputs.peak_mw[y]
        annual.append({
            "year": y, "demand_gwh": _r(demand, 1), "supply_requirement_gwh": _r(supply, 1),
            "generation_gwh": {t: _r(v, 1) for t, v in gen_t.items()}, "imports_gwh": _r(imp, 1),
            "unserved_gwh": _r(uns, 2), "curtailment_gwh": {t: _r(v, 1) for t, v in curt_t.items()},
            "renewable_share": _r(re / supply, 4), "gas_share": _r(gas / supply, 4),
            "import_share": _r(imp / supply, 4), "dependence_share": _r(dep / supply, 4),
            "emissions_mt": _r(em, 4), "capacity_mw": {t: _r(cap[(t, y)], 1) for t in T},
            "new_capacity_mw": {t: _r(d.new_mw.get((t, y), 0.0), 1) for t in T},
            "firm_capacity_mw": _r(firm, 1), "peak_demand_mw": _r(peak, 1),
            "reserve_margin_achieved": _r(firm / peak - 1, 4),
            "investment_musd": _r(inv, 2), "annualized_capital_musd": _r(ann_cap, 2),
            "fixed_om_musd": _r(fom, 2), "variable_cost_musd": _r(vom, 2), "import_cost_musd": _r(impc, 2),
            "total_cost_musd": _r(total, 2), "discounted_cost_musd": _r(total * df, 2),
        })
        for m in M:
            monthly.append({
                "year": y, "month": m, "demand_gwh": _r(inputs.demand_gwh[(y, m)], 2),
                "supply_requirement_gwh": _r(inputs.supply_req_gwh[(y, m)], 2),
                "generation_gwh": {t: _r(d.gen_gwh.get((t, y, m), 0.0), 2) for t in T},
                "imports_gwh": _r(d.imp_gwh.get((y, m), 0.0), 2),
                "unserved_gwh": _r(d.unserved_gwh.get((y, m), 0.0), 3),
                "demand_source": inputs.demand.loc[(inputs.demand["year"] == y) & (inputs.demand["month"] == m),
                                                   "source"].iloc[0],
            })
        tot["cost"] += total * df
        tot["inv"] += inv
        tot["em"] += em
        tot["re"] += re
        tot["supply"] += supply
        tot["gas"] += gas
        tot["imp"] += imp
        tot["dep"] += dep
        tot["uns"] += uns
        tot["curt"] += sum(curt_t.values())

    last = annual[-1]
    base = inputs.baseline_emissions_mt
    summary = {
        "total_cost_npv_musd": _r(tot["cost"], 1),
        "total_investment_musd": _r(tot["inv"], 1),
        "total_emissions_mt": _r(tot["em"], 3),
        "final_year": last["year"],
        "final_year_emissions_mt": last["emissions_mt"],
        "emission_change_vs_2024_pct": _r(100 * (last["emissions_mt"] - base) / base, 2) if base else None,
        "renewable_share_final_year": last["renewable_share"],
        "renewable_share_horizon": _r(tot["re"] / tot["supply"], 4),
        "gas_share_final_year": last["gas_share"],
        "import_share_horizon": _r(tot["imp"] / tot["supply"], 4),
        "dependence_share_final_year": last["dependence_share"],
        "dependence_share_horizon": _r(tot["dep"] / tot["supply"], 4),
        "total_new_capacity_mw": {t: _r(sum(d.new_mw.get((t, y), 0.0) for y in Y), 1) for t in T},
        "final_capacity_mw": last["capacity_mw"],
        "total_unserved_gwh": _r(tot["uns"], 2),
        "total_curtailment_gwh": _r(tot["curt"], 1),
        "min_reserve_margin": min(a["reserve_margin_achieved"] for a in annual),
        "baseline_2024_emissions_mt": _r(base, 4),
    }
    objectives = {
        "min_cost": _r(tot["cost"], 3), "min_emissions": _r(tot["em"], 4),
        "min_dependence": _r(tot["dep"], 2), "max_renewables": _r(tot["re"], 2),
        "min_investment": _r(tot["inv"], 3),
    }
    return {"annual": annual, "monthly": monthly, "summary": summary, "objective_values": objectives}
