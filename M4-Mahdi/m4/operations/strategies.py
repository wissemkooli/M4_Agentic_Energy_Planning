"""Alternative operating strategies for one short-term operational problem.

Each strategy is a DIFFERENT optimization of the same MILP (same data, same constraints):

  least_cost_operations  single: minimize operating cost (incl. value of lost load)
  reliability_first      lexicographic: minimize unserved energy, then cost; reserve sized on the
                         M1 upper 80 % bound (protection against forecast error) when it is feasible
  min_gas_use            lexicographic: minimize gas-fired generation (fuel use, emissions), then cost,
                         without more unserved energy than the least-cost plan
  balanced_operations    epsilon-constraint: minimize cost with gas use bounded halfway between the
                         least-cost and minimum-gas plans (only when those differ by more than 1 %)

Strategies whose monthly decisions are identical (within 0.5 unit / 1 GWh) are flagged as duplicates.
"""
from __future__ import annotations

from ..data_integration.repository import InputBundle
from .engine import compute_op_indicators, run_operational, simulate_status_quo
from .inputs import OperationalInputs, build_operational_inputs
from .schema import OperationalProblem, OpObjectiveConfig

OP_TEMPLATES = {
    "least_cost_operations": {
        "name": "Least-cost operation",
        "objective": "Minimize operating cost (fuel, imports, units in service and unserved energy at the value of lost load)",
    },
    "reliability_first": {
        "name": "Reliability first",
        "objective": "Minimize unserved energy first, then cost; reserve sized on the M1 upper 80 % demand bound when achievable",
    },
    "min_gas_use": {
        "name": "Minimum gas consumption",
        "objective": "Minimize gas-fired generation (fuel and emissions) first, then cost, with no more unserved energy than the least-cost plan",
    },
    "balanced_operations": {
        "name": "Balanced cost and fuel use",
        "objective": "Minimize cost with gas use bounded halfway between the least-cost and minimum-gas plans",
    },
}
SIGNIFICANCE = 0.01


def _signature(run: dict) -> tuple[list[float], list[float]]:
    units, energy = [], []
    for m in run["results"]["monthly"]:
        units += list(m["units_in_service"].values())
        energy += list(m["generation_gwh"].values()) + [m["imports_gwh"], m["unserved_gwh"]]
    return units, energy


def _same(a: dict, b: dict) -> bool:
    """Identical decisions: same units in service (< 0.5 unit apart) and same energy (< 1 GWh apart)."""
    (ua, ea), (ub, eb) = _signature(a), _signature(b)
    if len(ua) != len(ub) or len(ea) != len(eb):
        return False
    return all(abs(x - y) < 0.5 for x, y in zip(ua, ub)) and all(abs(x - y) <= 1.0 for x, y in zip(ea, eb))


def _reindicate(run: dict, inp: OperationalInputs) -> None:
    """Re-evaluate a run solved on alternative inputs against the base inputs (same peak for every strategy)."""
    units, gen, imp, uns = {}, {}, {}, {}
    for m in run["results"]["monthly"]:
        p = (m["year"], m["month"])
        units.update({(t, p): v for t, v in m["units_in_service"].items()})
        gen.update({(t, p): v for t, v in m["generation_gwh"].items()})
        imp[p], uns[p] = m["imports_gwh"], m["unserved_gwh"]
    run["results_on_solved_inputs"] = {"summary": run["results"]["summary"],
                                       "note": "indicators measured against the reserve basis used in the solve"}
    run["results"] = compute_op_indicators(inp, units, gen, imp, uns)
    run["objective_values"] = run["results"]["objective_values"]


METRICS = [
    ("operating_cost_musd", "lowest operating cost", "highest operating cost", min, "MUSD"),
    ("unserved_gwh", "least unserved energy", "most unserved energy", min, "GWh"),
    ("gas_gwh", "lowest gas consumption", "highest gas consumption", min, "GWh"),
    ("min_reserve_margin", "highest minimum reserve margin", "lowest minimum reserve margin", max, "%"),
    ("imports_gwh", "lowest import dependence", "highest import dependence", min, "GWh"),
]


def _compare(s: dict, peers: list[dict]) -> tuple[list[str], list[str]]:
    adv, dis = [], []
    distinct = [p for p in peers if not p.get("duplicate_of")]
    if len(distinct) < 2:
        return adv, dis
    me = s["run"]["results"]["summary"]
    for key, good, bad, fn, unit in METRICS:
        vals = [p["run"]["results"]["summary"][key] for p in distinct]
        best, worst = fn(vals), (max(vals) if fn is min else min(vals))
        if abs(best - worst) <= max(SIGNIFICANCE * max(abs(best), abs(worst)), 1e-3):
            continue
        val = me[key] * 100 if unit == "%" else me[key]
        txt = f"{val:,.1f} {unit}"
        if abs(me[key] - best) <= 1e-9 * max(1, abs(best)):
            adv.append(f"Has the {good} among the alternatives ({txt}).")
        elif abs(me[key] - worst) <= 1e-9 * max(1, abs(worst)):
            dis.append(f"Has the {bad} among the alternatives ({txt}).")
    return adv, dis


def _vs_least_cost(s: dict, peers: list[dict]) -> dict | None:
    ref = next((p for p in peers if p["key"] == "least_cost_operations" and p["feasible"]), None)
    if ref is None or s["key"] == "least_cost_operations":
        return None
    a, b = s["run"]["results"]["summary"], ref["run"]["results"]["summary"]
    d_cost = a["operating_cost_musd"] - b["operating_cost_musd"]
    d_gas = b["gas_gwh"] - a["gas_gwh"]
    return {"reference_strategy": "least_cost_operations",
            "additional_operating_cost_musd": round(d_cost, 3),
            "additional_cost_pct": round(100 * d_cost / b["operating_cost_musd"], 2) if b["operating_cost_musd"] else None,
            "gas_saved_gwh": round(d_gas, 2), "emissions_avoided_mt": round(b["emissions_mt"] - a["emissions_mt"], 5),
            "unserved_reduced_gwh": round(b["unserved_gwh"] - a["unserved_gwh"], 3),
            "reserve_margin_change_pp": round(100 * (a["min_reserve_margin"] - b["min_reserve_margin"]), 2),
            "cost_per_mwh_gas_saved_usd": round(d_cost * 1000 / d_gas, 2) if d_gas > 1e-3 else None}


def _risks(run: dict, inp: OperationalInputs) -> list[str]:
    s = run["results"]["summary"]
    risks = []
    if inp.data_status != "REAL":
        risks.append(f"Inputs are {inp.data_status}: the schedule illustrates the method and must not be used to operate "
                     "the real system until real M1 forecasts and fleet data are integrated.")
    if s["unserved_gwh"] > 1e-3:
        risks.append(f"{s['unserved_gwh']:,.1f} GWh of demand is not supplied: load shedding would be required.")
    if s["min_reserve_margin"] < inp.reserve_margin + 0.02:
        risks.append(f"Reserve margin falls to {s['min_reserve_margin'] * 100:.1f} % in at least one month: an unplanned "
                     "unit outage could cause supply interruptions.")
    if inp.import_enabled and any(m["imports_gwh"] >= inp.max_import_share * m["demand_gwh"] - 1e-3 and m["imports_gwh"] > 0
                                  for m in run["results"]["monthly"]):
        risks.append("Imports reach the interconnection limit in at least one month: exposure to availability and "
                     "price of imported electricity.")
    if any(m["demand_source"] != "M1_forecast" for m in run["results"]["monthly"]):
        risks.append("Part of the period uses an M4 extrapolation of demand beyond the M1 forecast horizon.")
    risks.append("Demand is an M1 forecast: actual demand may differ (see the M1 80 % interval).")
    risks.append("Monthly resolution: hourly peaks, ramping and start-up constraints are approximated.")
    return risks


def _schedule(run: dict, inp: OperationalInputs) -> list[dict]:
    out = []
    for m in run["results"]["monthly"]:
        acts = [f"Keep {int(round(v))} of {m['units_available'][t]} available {t} units in service" for t, v in m["units_in_service"].items()]
        acts.append("Generate " + ", ".join(f"{t} {g:,.0f} GWh" for t, g in m["generation_gwh"].items() if g >= 0.5))
        if m["imports_gwh"] >= 0.5:
            acts.append(f"Import {m['imports_gwh']:,.0f} GWh")
        if m["unserved_gwh"] >= 0.05:
            acts.append(f"Unserved energy {m['unserved_gwh']:,.1f} GWh (load shedding)")
        out.append({"period": m["period"], "actions": acts, "units_in_service": m["units_in_service"],
                    "operating_cost_musd": m["operating_cost_musd"], "reserve_margin": m["reserve_margin_achieved"],
                    "unserved_gwh": m["unserved_gwh"], "gas_gwh": m["gas_gwh"], "imports_gwh": m["imports_gwh"]})
    return out


def build_op_report(s: dict, peers: list[dict], inp: OperationalInputs) -> dict:
    run = s["run"]
    sm = run["results"]["summary"]
    adv, dis = _compare(s, peers)
    rel = _vs_least_cost(s, peers)
    if rel and not s.get("duplicate_of"):
        if rel["gas_saved_gwh"] > 0.5:
            adv.append(f"Uses {rel['gas_saved_gwh']:,.0f} GWh less gas-fired generation than the least-cost plan.")
        if rel["unserved_reduced_gwh"] > 0.05:
            adv.append(f"Serves {rel['unserved_reduced_gwh']:,.1f} GWh more demand than the least-cost plan.")
        if rel["reserve_margin_change_pp"] > 0.5:
            adv.append(f"Raises the minimum reserve margin by {rel['reserve_margin_change_pp']:.1f} percentage points.")
        if rel["additional_operating_cost_musd"] > 0.05:
            dis.append(f"Costs {rel['additional_operating_cost_musd']:,.2f} MUSD more (+{rel['additional_cost_pct']:.2f} %) "
                       "than the least-cost plan.")
    avg_units = sm["avg_units_in_service"]
    approach = (f"Keeps on average {avg_units:g} gas units in service, generates {sm['gas_gwh']:,.0f} GWh from gas, imports "
                f"{sm['imports_gwh']:,.0f} GWh and leaves {sm['unserved_gwh']:,.1f} GWh unserved over "
                f"{len(inp.periods)} month(s), for an operating cost of {sm['operating_cost_musd']:,.1f} MUSD.")
    return {
        "kind": "operational",
        "approach": approach,
        "headline": {"kind": "operational", **{k: sm[k] for k in (
            "operating_cost_musd", "total_economic_cost_musd", "unserved_gwh", "gas_gwh", "imports_gwh", "emissions_mt",
            "renewable_share", "min_reserve_margin", "avg_units_in_service", "curtailment_gwh", "avg_cost_usd_per_mwh",
            "demand_gwh")}},
        "tradeoff_vs_least_cost": rel,
        "schedule": _schedule(run, inp),
        "advantages": adv, "disadvantages": dis, "risks": _risks(run, inp),
        "assumptions": inp.assumptions, "data_status": inp.data_status,
    }


def select_op_strategy_keys(objectives: list[str]) -> list[str]:
    return ["least_cost_operations", "reliability_first", "min_gas_use", "balanced_operations"]


def generate_operational_strategies(bundle: InputBundle, problem: OperationalProblem,
                                    objectives: list[str] | None = None) -> dict:
    inp = build_operational_inputs(bundle, problem)
    runs: dict[str, dict] = {}
    notes: list[str] = []
    runs["least_cost_operations"] = run_operational(inp, OpObjectiveConfig(method="single", primary="min_operating_cost"))
    base = runs["least_cost_operations"]
    if not base["feasible"]:
        return {"status": "infeasible", "strategies": [], "diagnostics": base.get("diagnostics"),
                "message": base.get("message"), "base_run": base, "problem_type": "operational"}
    uns_cap = base["objective_values"]["min_unserved"] + 1e-3

    # reliability first: reserve sized on a higher M1 bound when the data has intervals and it stays feasible
    rel_basis = {"forecast": "upper_80", "lower_80": "forecast", "lower_95": "forecast",
                 "upper_80": "upper_95"}.get(problem.demand_basis)
    lex = OpObjectiveConfig(method="lexicographic", priorities=["min_unserved", "min_operating_cost"])
    rel_run = None
    if rel_basis and bundle.demand.has_intervals:
        try:
            inp_rel = build_operational_inputs(bundle, problem.model_copy(update={"reserve_demand_basis": rel_basis}))
            rel_run = run_operational(inp_rel, lex, diagnose=False)
            if rel_run["feasible"]:
                _reindicate(rel_run, inp)
                rel_run["notes"].append(f"Reserve sized on the M1 '{rel_basis}' demand bound; reported reserve margins "
                                        f"are measured against the '{problem.demand_basis}' peak like the other strategies.")
                notes.append(f"Reliability first: reserve sized on the M1 '{rel_basis}' demand bound.")
            else:
                notes.append(f"Reliability first: a reserve sized on the M1 '{rel_basis}' bound is not achievable with the "
                             "available units; the requested reserve basis is kept.")
                rel_run = None
        except Exception as exc:  # data problem for the alternative basis is reported, not hidden
            notes.append(f"Reliability first: alternative reserve basis unavailable ({exc}).")
            rel_run = None
    runs["reliability_first"] = rel_run or run_operational(inp, lex, diagnose=False)

    runs["min_gas_use"] = run_operational(inp, OpObjectiveConfig(method="lexicographic", priorities=["min_gas", "min_operating_cost"]),
                                          diagnose=False, extra_bounds={"min_unserved": uns_cap})
    keys = ["least_cost_operations", "reliability_first", "min_gas_use"]
    if runs["min_gas_use"]["feasible"]:
        g_cost, g_min = base["objective_values"]["min_gas"], runs["min_gas_use"]["objective_values"]["min_gas"]
        if g_cost - g_min > SIGNIFICANCE * max(g_cost, 1e-9):
            eps = g_min + 0.5 * (g_cost - g_min)
            runs["balanced_operations"] = run_operational(
                inp, OpObjectiveConfig(method="epsilon_constraint", primary="min_operating_cost", epsilon_bounds={"min_gas": eps}),
                diagnose=False, extra_bounds={"min_unserved": uns_cap})
            keys.append("balanced_operations")
        else:
            notes.append("Balanced strategy not computed: least-cost and minimum-gas plans use the same amount of gas "
                         "(within 1 %), so there is no trade-off to balance.")

    strategies = []
    for key in keys:
        run = runs[key]
        dup = None
        if run.get("feasible"):
            for prev in strategies:
                if prev["feasible"] and _same(run, runs[prev["key"]]):
                    dup = prev["key"]
                    break
        strategies.append({"key": key, "name": OP_TEMPLATES[key]["name"], "strategic_objective": OP_TEMPLATES[key]["objective"],
                           "feasible": bool(run.get("feasible")), "status": run.get("status"), "duplicate_of": dup, "run": run})
    feasible = [s for s in strategies if s["feasible"]]
    for s in strategies:
        s["report"] = build_op_report(s, feasible, inp) if s["feasible"] else None
    distinct = [s for s in feasible if not s["duplicate_of"]]
    return {"status": "ok", "problem_type": "operational", "strategies": strategies, "n_feasible": len(feasible),
            "n_distinct": len(distinct), "notes": notes, "status_quo": simulate_status_quo(inp),
            "message": f"{len(feasible)} feasible operating strategies, {len(distinct)} with distinct decisions."}
