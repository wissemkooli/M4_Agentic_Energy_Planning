"""Strategy reports: roadmap, milestones, advantages, disadvantages and risks.

Everything in a report is derived deterministically from validated optimization results
and from comparisons with the other feasible strategies of the same planning request.
No number is produced by a language model.
"""
from __future__ import annotations

from ..modeling.system import SystemInputs


def _fmt_mw(x: float) -> str:
    return f"{x:,.0f} MW"


def build_roadmap(run: dict, inp: SystemInputs) -> list[dict]:
    annual = run["results"]["annual"]
    roadmap = []
    for a in annual:
        actions = [f"Commission {_fmt_mw(v)} of {t}" for t, v in a["new_capacity_mw"].items() if v >= 1]
        roadmap.append({
            "year": a["year"], "actions": actions or ["No new capacity commissioned"],
            "investment_musd": a["investment_musd"], "new_capacity_mw": a["new_capacity_mw"],
            "renewable_share": a["renewable_share"], "emissions_mt": a["emissions_mt"],
            "gas_share": a["gas_share"], "reserve_margin": a["reserve_margin_achieved"],
        })
    return roadmap


def build_milestones(run: dict, inp: SystemInputs) -> list[dict]:
    annual = {a["year"]: a for a in run["results"]["annual"]}
    ys = set([inp.years[0], inp.years[-1]]) | {y for y in inp.years if y % 5 == 0}
    ys |= set(inp.re_min_share) & {min(inp.re_min_share, default=0)} if inp.re_min_share else set()
    out = []
    cumulative = 0.0
    for y in inp.years:
        cumulative += annual[y]["investment_musd"]
        if y in ys:
            a = annual[y]
            out.append({"year": y, "capacity_mw": a["capacity_mw"], "renewable_share": a["renewable_share"],
                        "gas_share": a["gas_share"], "emissions_mt": a["emissions_mt"],
                        "demand_gwh": a["demand_gwh"], "cumulative_investment_musd": round(cumulative, 1),
                        "reserve_margin": a["reserve_margin_achieved"]})
    return out


SIGNIFICANCE = 0.01

METRICS = [
    ("total_cost_npv_musd", "lowest total system cost", "highest total system cost", min),
    ("total_emissions_mt", "lowest cumulative CO2 emissions", "highest cumulative CO2 emissions", min),
    ("renewable_share_final_year", "highest final-year renewable share", "lowest final-year renewable share", max),
    ("dependence_share_horizon", "lowest dependence on gas and imports", "highest dependence on gas and imports", min),
    ("total_investment_musd", "lowest investment requirement", "highest investment requirement", min),
]


def compare_tradeoffs(strategy: dict, peers: list[dict]) -> tuple[list[str], list[str]]:
    adv, dis = [], []
    s = strategy["run"]["results"]["summary"]
    distinct = [p for p in peers if not p.get("duplicate_of")]
    if len(distinct) < 2:
        return adv, dis
    for key, good, bad, best_fn in METRICS:
        vals = [p["run"]["results"]["summary"][key] for p in distinct]
        best, worst = best_fn(vals), (max(vals) if best_fn is min else min(vals))
        # differences below 1 % (relative) are not reported as advantages/disadvantages
        if abs(best - worst) <= SIGNIFICANCE * max(abs(best), abs(worst), 1e-9):
            continue
        unit = "%" if "share" in key else ("MtCO2" if "emissions" in key else "MUSD")
        val = s[key] * 100 if "share" in key else s[key]
        txt = f"{val:,.1f} {unit}"
        if abs(s[key] - best) <= 1e-9 * max(1, abs(best)):
            adv.append(f"Has the {good} among the alternatives ({txt}).")
        elif abs(s[key] - worst) <= 1e-9 * max(1, abs(worst)):
            dis.append(f"Has the {bad} among the alternatives ({txt}).")
    return adv, dis


def relative_to_reference(strategy: dict, peers: list[dict]) -> dict | None:
    """Trade-off metrics versus the cost-conscious (least-cost) strategy of the same request."""
    ref = next((p for p in peers if p["key"] == "cost_conscious"), None)
    if ref is None or strategy["key"] == "cost_conscious":
        return None
    a, b = strategy["run"]["results"]["summary"], ref["run"]["results"]["summary"]
    d_cost = a["total_cost_npv_musd"] - b["total_cost_npv_musd"]
    d_em = b["total_emissions_mt"] - a["total_emissions_mt"]  # emissions avoided (Mt)
    return {
        "reference_strategy": "cost_conscious",
        "additional_cost_npv_musd": round(d_cost, 1),
        "additional_cost_pct": round(100 * d_cost / b["total_cost_npv_musd"], 2),
        "emissions_avoided_mt": round(d_em, 3),
        "emissions_avoided_pct": round(100 * d_em / b["total_emissions_mt"], 2) if b["total_emissions_mt"] else None,
        # MUSD / MtCO2 = USD / tCO2 (discounted cost over undiscounted cumulative emissions)
        "implied_abatement_cost_usd_per_t": round(d_cost / d_em, 1) if d_em > 1e-6 else None,
        "additional_investment_musd": round(a["total_investment_musd"] - b["total_investment_musd"], 1),
        "renewable_share_change_pp": round(100 * (a["renewable_share_final_year"] - b["renewable_share_final_year"]), 2),
    }


def identify_risks(run: dict, inp: SystemInputs) -> list[str]:
    risks = []
    res = run["results"]
    s = res["summary"]
    last = res["annual"][-1]
    if inp.data_status != "REAL":
        risks.append(f"Inputs are {inp.data_status}: the results illustrate the method and must not be used as "
                     "an actual investment recommendation until real M1/M3 and reference data are integrated.")
    if inp.demand_meta["extrapolated_years"]:
        risks.append(f"Demand for {inp.demand_meta['extrapolated_years'][0]}-{inp.demand_meta['extrapolated_years'][-1]} "
                     "is an M4 extrapolation beyond the M1 forecast horizon.")
    vre_share = sum(last["generation_gwh"].get(t, 0) for t in inp.variable_techs) / last["supply_requirement_gwh"]
    if vre_share >= inp.max_vre_share * 0.95:
        risks.append(f"Variable renewables reach {vre_share * 100:.0f} % of supply in {last['year']}, close to the "
                     f"{inp.max_vre_share * 100:.0f} % integration limit: grid flexibility (storage, interconnection, "
                     "demand response) not modelled at monthly resolution becomes critical.")
    if last["gas_share"] > 0.5:
        risks.append(f"Gas still supplies {last['gas_share'] * 100:.0f} % of electricity in {last['year']}: "
                     "exposure to gas price and supply risks remains high.")
    if inp.budget_total and s["total_investment_musd"] >= inp.budget_total * 0.99:
        risks.append("The investment budget is fully used: cost overruns would make the plan infeasible.")
    if s["min_reserve_margin"] <= inp.reserve_margin + 0.005:
        risks.append(f"Reserve margin is at its minimum ({inp.reserve_margin * 100:.0f} %) in at least one year: "
                     "delays in commissioning firm capacity would reduce reliability.")
    if s["total_curtailment_gwh"] > 0.05 * sum(a["supply_requirement_gwh"] for a in res["annual"]):
        risks.append(f"Curtailment of {s['total_curtailment_gwh']:,.0f} GWh over the horizon indicates renewable "
                     "capacity beyond what monthly demand can absorb.")
    risks.append("Monthly resolution approximates peak demand and intra-day variability (peak proxy and VRE limit).")
    return risks


def build_strategy_report(strategy: dict, feasible_peers: list[dict], inp: SystemInputs) -> dict:
    run = strategy["run"]
    s = run["results"]["summary"]
    adv, dis = compare_tradeoffs(strategy, feasible_peers)
    rel = relative_to_reference(strategy, feasible_peers)
    if rel and not strategy.get("duplicate_of"):
        if rel["emissions_avoided_mt"] > 0.01:
            txt = (f"Avoids {rel['emissions_avoided_mt']:,.1f} MtCO2 ({rel['emissions_avoided_pct']:.1f} %) compared "
                   f"with the least-cost plan")
            if rel["implied_abatement_cost_usd_per_t"] is not None and rel["additional_cost_npv_musd"] > 0:
                txt += f", at an implied abatement cost of {rel['implied_abatement_cost_usd_per_t']:,.0f} USD/tCO2"
            adv.append(txt + ".")
        if rel["additional_cost_npv_musd"] > 0.5:
            dis.append(f"Costs {rel['additional_cost_npv_musd']:,.0f} MUSD more (NPV, +{rel['additional_cost_pct']:.1f} %) "
                       "than the least-cost plan.")
    added = {t: v for t, v in s["total_new_capacity_mw"].items() if v >= 1}
    approach = ("Adds " + ", ".join(f"{_fmt_mw(v)} of {t}" for t, v in added.items())
                + f" between {inp.years[0]} and {inp.years[-1]}, for {s['total_investment_musd']:,.0f} MUSD "
                f"of overnight investment.") if added else "Relies on the existing fleet without new capacity."
    return {
        "approach": approach,
        "headline": {
            "total_cost_npv_musd": s["total_cost_npv_musd"], "total_investment_musd": s["total_investment_musd"],
            "total_emissions_mt": s["total_emissions_mt"], "final_year_emissions_mt": s["final_year_emissions_mt"],
            "emission_change_vs_2024_pct": s["emission_change_vs_2024_pct"],
            "renewable_share_final_year": s["renewable_share_final_year"],
            "gas_share_final_year": s["gas_share_final_year"],
            "dependence_share_horizon": s["dependence_share_horizon"],
            "min_reserve_margin": s["min_reserve_margin"],
        },
        "tradeoff_vs_least_cost": rel,
        "roadmap": build_roadmap(run, inp),
        "milestones": build_milestones(run, inp),
        "advantages": adv,
        "disadvantages": dis,
        "risks": identify_risks(run, inp),
        "assumptions": inp.assumptions,
        "data_status": inp.data_status,
    }
