"""Solving, evaluating and validating short-term operational problems.

- `run_operational`: single, lexicographic or epsilon-constraint solve of the MILP;
  returns a structured, independently validated result (never a solution unless optimal).
- `simulate_status_quo`: merit-order operation with every available unit in service,
  the reference against which optimized strategies are compared.
- `diagnose_operational_infeasibility`: elastic reformulation explaining why no plan exists.
All indicators (cost, unserved energy, gas use, reserve) are recomputed from the extracted
solution by the same function for simulation and optimization.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pyomo.environ as pyo

from ..optimization.solver import solve
from .formulation import describe_op_formulation
from .inputs import OperationalInputs
from .model import ELASTIC_WEIGHTS, add_bound, build_op_model, set_objective
from .schema import OP_OBJECTIVE_LABELS, OP_OBJECTIVE_UNITS, OpObjectiveConfig

MIP_GAP = 1e-6
TIE_BREAK = 1e-6
TOL = 1e-4


def _terms(primary: str) -> dict[str, float]:
    return {primary: 1.0} if primary == "min_operating_cost" else {primary: 1.0, "min_operating_cost": TIE_BREAK}


# ----------------------------------------------------------------------------- indicators
def compute_op_indicators(inp: OperationalInputs, units: dict, gen: dict, imp: dict, uns: dict) -> dict:
    monthly = []
    tot = dict(var=0.0, fixed=0.0, uns_cost=0.0, uns=0.0, gas=0.0, imp=0.0, em=0.0, re=0.0, dem=0.0, curt=0.0)
    for p in inp.periods:
        d = inp.demand_gwh[p]
        g = {t: gen[(t, p)] for t in inp.techs}
        var = sum(inp.techs[t].variable_cost * g[t] for t in inp.techs) / 1000.0 + inp.import_cost * imp[p] / 1000.0
        n_units = {t: units[(t, p)] for t in inp.dispatchable}
        fixed = inp.unit_fixed_cost * sum(n_units.values())
        uc = inp.voll * uns[p] / 1000.0
        gas = sum(g[t] for t in inp.gas_techs)
        re = sum(g[t] for t in inp.renewable_techs)
        em = sum(inp.techs[t].emission_factor * g[t] for t in inp.techs) / 1000.0 + inp.import_ef * imp[p] / 1000.0
        curt = sum(max(0.0, inp.potential_gwh(t, p) - g[t]) for t in inp.variable_techs)
        firm = inp.firm_capacity_mw(units, p)
        monthly.append({
            "period": inp.labels[p], "year": p[0], "month": p[1], "hours": inp.hours[p],
            "demand_gwh": round(d, 3), "m1_demand_gwh": round(inp.demand_m1_gwh[p], 3),
            "demand_source": inp.demand_source[p], "peak_mw": round(inp.peak_mw[p], 1),
            "generation_gwh": {t: round(v, 3) for t, v in g.items()}, "imports_gwh": round(imp[p], 3),
            "unserved_gwh": round(uns[p], 3), "units_in_service": {t: round(v, 3) for t, v in n_units.items()},
            "units_available": {t: inp.units_available[(t, p)] for t in inp.dispatchable},
            "units_total": {t: inp.units_total[(t, p)] for t in inp.dispatchable},
            "firm_capacity_mw": round(firm, 1), "reserve_margin_achieved": round(firm / inp.peak_mw[p] - 1, 4),
            "variable_cost_musd": round(var, 3), "fixed_cost_musd": round(fixed, 3), "unserved_cost_musd": round(uc, 3),
            "operating_cost_musd": round(var + fixed, 3), "gas_gwh": round(gas, 3), "emissions_mt": round(em, 5),
            "renewable_share": round(re / d, 4) if d else 0.0, "gas_share": round(gas / d, 4) if d else 0.0,
            "import_share": round(imp[p] / d, 4) if d else 0.0, "curtailment_gwh": round(curt, 3),
        })
        for k, v in (("var", var), ("fixed", fixed), ("uns_cost", uc), ("uns", uns[p]), ("gas", gas), ("imp", imp[p]),
                     ("em", em), ("re", re), ("dem", d), ("curt", curt)):
            tot[k] += v
    unit_months = [sum(m["units_in_service"].values()) for m in monthly]
    summary = {
        "periods": [m["period"] for m in monthly],
        "demand_gwh": round(tot["dem"], 2),
        "operating_cost_musd": round(tot["var"] + tot["fixed"], 3),
        "variable_cost_musd": round(tot["var"], 3), "fixed_cost_musd": round(tot["fixed"], 3),
        "unserved_cost_musd": round(tot["uns_cost"], 3),
        "total_economic_cost_musd": round(tot["var"] + tot["fixed"] + tot["uns_cost"], 3),
        "unserved_gwh": round(tot["uns"], 3), "demand_served_share": round(1 - tot["uns"] / tot["dem"], 5) if tot["dem"] else 1.0,
        "gas_gwh": round(tot["gas"], 2), "imports_gwh": round(tot["imp"], 2), "emissions_mt": round(tot["em"], 5),
        "renewable_share": round(tot["re"] / tot["dem"], 4) if tot["dem"] else 0.0,
        "gas_share": round(tot["gas"] / tot["dem"], 4) if tot["dem"] else 0.0,
        "curtailment_gwh": round(tot["curt"], 2),
        "min_reserve_margin": min(m["reserve_margin_achieved"] for m in monthly),
        "avg_units_in_service": round(sum(unit_months) / len(unit_months), 2),
        "avg_cost_usd_per_mwh": round((tot["var"] + tot["fixed"]) * 1000 / (tot["dem"] - tot["uns"]), 2)
        if tot["dem"] - tot["uns"] > 0 else None,
    }
    objective_values = {"min_operating_cost": summary["total_economic_cost_musd"], "min_unserved": summary["unserved_gwh"],
                        "min_gas": summary["gas_gwh"], "min_emissions": summary["emissions_mt"]}
    return {"monthly": monthly, "summary": summary, "objective_values": objective_values}


# ----------------------------------------------------------------------------- validation
def validate_op_solution(inp: OperationalInputs, m, units, gen, imp, uns, results: dict) -> dict:
    per = inp.periods
    checks = []
    bal = max(abs(sum(gen[(t, p)] for t in inp.techs) + imp[p] + uns[p] - inp.demand_gwh[p]) for p in per)
    checks.append({"check": "Monthly energy balance", "max_residual_gwh": round(bal, 6), "passed": bal <= 1e-3})
    over = 0.0
    for p in per:
        for t in inp.techs:
            limit = units[(t, p)] * inp.unit_output_gwh(t, p) if t in inp.dispatchable else inp.potential_gwh(t, p)
            over = max(over, gen[(t, p)] - limit)
    checks.append({"check": "Generation within units in service / available capacity", "max_excess_gwh": round(over, 6),
                   "passed": over <= 1e-3})
    if inp.unit_commitment:
        frac = max((abs(units[k] - round(units[k])) for k in units), default=0.0)
        checks.append({"check": "Integer number of units in service", "max_fractionality": round(frac, 8), "passed": frac <= 1e-6})
        below = max((inp.min_stable * units[(t, p)] * inp.unit_output_gwh(t, p) - gen[(t, p)]
                     for t in inp.dispatchable for p in per), default=0.0)
        checks.append({"check": "Minimum stable output of units in service", "max_shortfall_gwh": round(max(0, below), 6),
                       "passed": below <= 1e-3})
    bounds = [k for k, v in units.items() if v > inp.units_available[k] + 1e-6]
    checks.append({"check": "Units in service <= units available", "violations": [f"{t} {inp.labels[p]}" for t, p in bounds],
                   "passed": not bounds})
    neg = min(min(gen.values()), min(imp.values()), min(uns.values()), min(units.values(), default=0.0))
    checks.append({"check": "Non-negativity", "min_value": round(neg, 6), "passed": neg >= -1e-6})

    rows = []
    for mo in results["monthly"]:
        p = next(x for x in per if inp.labels[x] == mo["period"])
        req = (1 + inp.reserve_margin) * inp.peak_mw[p]
        rows.append({"constraint": "Operating reserve", "scope": mo["period"], "value": mo["firm_capacity_mw"],
                     "limit": round(req, 1), "relation": ">=", "unit": "MW",
                     "satisfied": mo["firm_capacity_mw"] >= req - TOL * req, "margin": round(mo["firm_capacity_mw"] - req, 2)})
        if inp.import_enabled:
            lim = inp.max_import_share * inp.demand_gwh[p]
            rows.append({"constraint": "Import limit", "scope": mo["period"], "value": mo["imports_gwh"], "limit": round(lim, 3),
                         "relation": "<=", "unit": "GWh", "satisfied": mo["imports_gwh"] <= lim + 1e-3,
                         "margin": round(lim - mo["imports_gwh"], 3)})
    if inp.gas_limit_gwh is not None:
        g = results["summary"]["gas_gwh"]
        rows.append({"constraint": "Gas generation limit", "scope": "period", "value": g, "limit": inp.gas_limit_gwh,
                     "relation": "<=", "unit": "GWh", "satisfied": g <= inp.gas_limit_gwh + 1e-2,
                     "margin": round(inp.gas_limit_gwh - g, 2)})
    if not inp.allow_unserved:
        u = results["summary"]["unserved_gwh"]
        rows.append({"constraint": "No unserved energy", "scope": "period", "value": u, "limit": 0.0, "relation": "<=",
                     "unit": "GWh", "satisfied": u <= 1e-3, "margin": round(-u, 3)})

    obj = []
    for k, e in m.obj_expr.items():
        solver_v, recomputed = float(pyo.value(e)), results["objective_values"][k]
        diff = abs(solver_v - recomputed)
        obj.append({"objective": k, "solver_value": round(solver_v, 4), "recomputed": recomputed,
                    "abs_diff": round(diff, 6), "passed": diff <= 1e-3 + 1e-5 * abs(solver_v)})
    ok = all(c["passed"] for c in checks) and all(r["satisfied"] for r in rows) and all(o["passed"] for o in obj)
    return {"status": "valid" if ok else "issues", "physical_checks": checks, "constraints": rows,
            "objective_consistency": obj,
            "method": "independent recomputation from the extracted solution (balance, unit limits, integrality, "
                      "minimum output, reserve, objective values)"}


# ----------------------------------------------------------------------------- solve
def _extract(inp: OperationalInputs, m):
    per = inp.periods
    units = {(d, per[i]): max(0.0, float(pyo.value(m.units[d, i]))) for d in inp.dispatchable for i in m.P}
    if inp.unit_commitment:
        units = {k: float(round(v)) if abs(v - round(v)) <= 1e-6 else v for k, v in units.items()}
    gen = {(t, per[i]): max(0.0, float(pyo.value(m.gen[t, i]))) for t in inp.techs for i in m.P}
    imp = {per[i]: max(0.0, float(pyo.value(m.imp[i]))) for i in m.P}
    uns = {per[i]: max(0.0, float(pyo.value(m.uns[i]))) for i in m.P}
    return units, gen, imp, uns


MESSAGES = {"reserve": ("Operating reserve cannot be met with the units available", "MW of firm capacity missing"),
            "gas_limit": ("Gas (fuel) limit too low to supply demand", "GWh of gas generation above the limit"),
            "unserved_energy": ("Demand cannot be fully supplied", "GWh")}


def diagnose_operational_infeasibility(inp: OperationalInputs) -> dict:
    m = build_op_model(inp, elastic=True)
    m.objective = pyo.Objective(expr=m.elastic_penalty + 1e-6 * m.obj_expr["min_operating_cost"], sense=pyo.minimize)
    out = solve(m, mip_gap=MIP_GAP)
    if not out.ok:
        return {"status": "hard_limits_conflict", "violations": [],
                "summary": "Even with the reserve, fuel and supply constraints relaxed the problem is infeasible."}
    violations = []
    for name, var_name in m.slack_names:
        v = getattr(m, var_name)
        items = [(i, float(pyo.value(v[i]))) for i in v] if v.is_indexed() else [(None, float(pyo.value(v)))]
        nz = [(i, x) for i, x in items if x > 1e-4]
        if not nz:
            continue
        title, unit = MESSAGES[name]
        periods = [inp.labels[inp.periods[i]] for i, _ in nz if i is not None]
        violations.append({"constraint": name, "title": title, "unit": unit, "total_shortfall": round(sum(x for _, x in nz), 3),
                           "periods": periods, "years": periods,
                           "max_shortfall": round(max(x for _, x in nz), 3)})
    summary = ("Minimum relaxation needed to restore feasibility — " + "; ".join(
        f"{v['title']}" + (f" ({v['periods'][0]}–{v['periods'][-1]})" if v["periods"] else "")
        + f": {v['total_shortfall']:,.1f} {v['unit']}" + (f" (largest monthly gap {v['max_shortfall']:,.1f})" if v["periods"] else "")
        for v in violations) + ".") if violations else "No violated constraint found; infeasibility may be numerical."
    return {"status": "diagnosed", "summary": summary, "violations": violations,
            "method": f"elastic reformulation (weighted slacks: {ELASTIC_WEIGHTS})"}


def run_operational(inp: OperationalInputs, objective: OpObjectiveConfig | None = None, diagnose: bool = True,
                    extra_bounds: dict[str, float] | None = None) -> dict:
    objective = objective or inp.problem.objective
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    m = build_op_model(inp)
    build_s = time.perf_counter() - t0
    log, notes, final = [], [], None

    def run(step, terms):
        set_objective(m, terms)
        out = solve(m, mip_gap=MIP_GAP)
        log.append({"step": step, "status": out.status, "termination": out.termination, "seconds": round(out.seconds, 3),
                    "message": out.message, "solver": out.solver})
        return out

    for k, b in (extra_bounds or {}).items():
        add_bound(m, f"extra_{k}", k, b)
        notes.append(f"bound: {k} <= {b:,.4f} {OP_OBJECTIVE_UNITS[k]}")
    if objective.method == "single":
        final = run(f"single:{objective.primary}", _terms(objective.primary))
    elif objective.method == "epsilon_constraint":
        for k, b in objective.epsilon_bounds.items():
            add_bound(m, f"eps_{k}", k, b)
            notes.append(f"epsilon bound: {k} <= {b:,.4f} {OP_OBJECTIVE_UNITS[k]}")
        final = run(f"epsilon:{objective.primary}", _terms(objective.primary))
    else:
        tol = objective.lexicographic_tolerance_pct / 100.0
        for i, k in enumerate(objective.priorities):
            final = run(f"lexicographic[{i + 1}]:{k}", _terms(k))
            if not final.ok:
                break
            if i < len(objective.priorities) - 1:
                best = float(pyo.value(m.obj_expr[k]))
                bound = best + max(tol * abs(best), 1e-4)
                add_bound(m, f"lex_{k}", k, bound)
                notes.append(f"stage {i + 1}: {k} kept <= {bound:,.4f} {OP_OBJECTIVE_UNITS[k]}")

    base = {
        "problem_type": "operational", "method": objective.method, "objective_config": objective.model_dump(),
        "objective_description": OP_OBJECTIVE_LABELS[objective.priorities[0] if objective.method == "lexicographic" else objective.primary],
        "problem": inp.problem.model_dump(mode="json"), "formulation": describe_op_formulation(inp, objective),
        "data_status": inp.data_status, "provenance": inp.provenance, "assumptions": inp.assumptions,
        "solver": {"name": log[-1]["solver"] if log else None, "problem_class": "MILP" if inp.unit_commitment else "LP",
                   "mip_gap": MIP_GAP if inp.unit_commitment else None, "model_build_seconds": round(build_s, 3),
                   "solves": [{k: v for k, v in x.items() if k != "solver"} for x in log]},
        "notes": notes, "started_at": started.isoformat(),
    }
    if final is None or not final.ok:
        status = getattr(final, "status", "error")
        base.update({"status": status, "feasible": False, "results": None, "objective_values": None, "validation": None})
        if status == "infeasible" and diagnose:
            base["diagnostics"] = diagnose_operational_infeasibility(inp)
            base["message"] = "No operating plan satisfies all constraints. " + base["diagnostics"]["summary"]
        else:
            base["message"] = f"Solver terminated with status '{status}'; no solution is reported." + (
                " " + "; ".join(x["message"] for x in log if x["message"]) if any(x["message"] for x in log) else "")
        base["finished_at"] = datetime.now(timezone.utc).isoformat()
        return base
    units, gen, imp, uns = _extract(inp, m)
    results = compute_op_indicators(inp, units, gen, imp, uns)
    validation = validate_op_solution(inp, m, units, gen, imp, uns, results)
    base.update({"status": "optimal", "feasible": True, "results": results, "objective_values": results["objective_values"],
                 "validation": validation, "finished_at": datetime.now(timezone.utc).isoformat(),
                 "message": "Optimal solution found and independently validated." if validation["status"] == "valid"
                 else "Optimal solution found but validation reported issues."})
    return base


# ----------------------------------------------------------------------------- status quo
def simulate_status_quo(inp: OperationalInputs) -> dict:
    """Every available unit kept in service; merit-order dispatch (minimum output first)."""
    units, gen, imp, uns = {}, {}, {}, {}
    gas_left = inp.gas_limit_gwh if inp.gas_limit_gwh is not None else float("inf")
    for p in inp.periods:
        R = inp.demand_gwh[p]
        rem = R
        for d in inp.dispatchable:
            units[(d, p)] = float(inp.units_available[(d, p)])
            must = min(rem, inp.min_stable * units[(d, p)] * inp.unit_output_gwh(d, p) if inp.unit_commitment else 0.0,
                       gas_left if "gas" in d else float("inf"))
            gen[(d, p)] = must
            rem -= must
            if "gas" in d:
                gas_left -= must
        vre_room = inp.max_vre_share * R
        order = sorted([t for t in inp.techs if t not in inp.dispatchable], key=lambda t: inp.techs[t].variable_cost)
        order = sorted(order + inp.dispatchable + (["__imp__"] if inp.import_enabled else []),
                       key=lambda r: inp.import_cost if r == "__imp__" else inp.techs[r].variable_cost)
        imp[p] = 0.0
        for r in order:
            if r == "__imp__":
                q = min(rem, inp.max_import_share * R)
                imp[p] = q
            elif r in inp.dispatchable:
                room = units[(r, p)] * inp.unit_output_gwh(r, p) - gen[(r, p)]
                q = min(rem, room, gas_left if "gas" in r else float("inf"))
                gen[(r, p)] += q
                if "gas" in r:
                    gas_left -= q
            else:
                pot = inp.potential_gwh(r, p)
                if inp.techs[r].category == "variable":
                    pot = min(pot, vre_room)
                q = min(rem, pot)
                if inp.techs[r].category == "variable":
                    vre_room -= q
                gen[(r, p)] = q
            rem -= q
        uns[p] = max(0.0, rem)
    res = compute_op_indicators(inp, units, gen, imp, uns)
    issues = []
    for mo in res["monthly"]:
        if mo["unserved_gwh"] > 1e-3:
            issues.append(f"{mo['period']}: {mo['unserved_gwh']:,.1f} GWh of demand cannot be supplied")
        if mo["reserve_margin_achieved"] < inp.reserve_margin - 1e-6:
            issues.append(f"{mo['period']}: reserve margin {mo['reserve_margin_achieved'] * 100:.1f} % < required "
                          f"{inp.reserve_margin * 100:.1f} % even with every available unit in service")
    res.update({"method": "status quo: every available unit in service, merit-order dispatch", "issues": issues,
                "data_status": inp.data_status})
    return res
