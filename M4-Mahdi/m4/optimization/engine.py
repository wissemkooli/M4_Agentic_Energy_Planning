"""Optimization engine: single and multi-objective methods on top of the Pyomo model.

Methods
- single:             min f_primary
- weighted_sum:       min sum_k w_k (f_k - u_k) / (n_k - u_k)    (u: utopia, n: nadir from payoff table)
- epsilon_constraint: min f_primary  s.t.  f_k <= eps_k  for each bounded objective
- lexicographic:      for k in priorities: min f_k s.t. previous f_j kept within (1 + tol) of optimum
- pareto_frontier:    epsilon-constraint sweep between utopia and nadir of a secondary objective

Non-cost objectives receive a tiny cost tie-break (1e-6 x NPV) so the selected optimum
is not an arbitrary point among alternative optima. Reported objective values are always
recomputed from the solution, not taken from the weighted objective.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pyomo.environ as pyo

from ..modeling.indicators import Dispatch, compute_indicators
from ..modeling.system import SystemInputs
from ..scenarios.schema import OBJECTIVE_LABELS, OBJECTIVE_UNITS, ObjectiveConfig
from .diagnostics import diagnose_infeasibility
from .formulation import describe_formulation
from .model import SENSE, add_bound, build_model, set_objective
from .solver import solve
from .validation import validate_solution

TIE_BREAK = 1e-6


def _terms(primary: str) -> dict[str, float]:
    return {primary: 1.0} if primary == "min_cost" else {primary: 1.0, "min_cost": TIE_BREAK}


def _extract(inp: SystemInputs, m):
    new = {(t, y): max(0.0, float(pyo.value(m.new[t, y]))) for t in inp.techs for y in inp.years}
    gen = {(t, y, mo): max(0.0, float(pyo.value(m.gen[t, y, mo]))) for t in inp.techs for y in inp.years for mo in inp.months}
    imp = {(y, mo): max(0.0, float(pyo.value(m.imp[y, mo]))) for y in inp.years for mo in inp.months}
    return new, gen, imp


def _obj_values(m) -> dict[str, float]:
    return {k: float(pyo.value(e)) for k, e in m.obj_expr.items()}


class _Runner:
    def __init__(self, inp: SystemInputs):
        self.inp = inp
        t0 = time.perf_counter()
        self.model = build_model(inp)
        self.build_seconds = time.perf_counter() - t0
        self.log: list[dict] = []
        self.solver_name = None

    def solve(self, step: str, terms: dict[str, float]):
        set_objective(self.model, terms)
        out = solve(self.model)
        self.solver_name = out.solver
        self.log.append({"step": step, "status": out.status, "termination": out.termination,
                         "seconds": round(out.seconds, 3), "message": out.message})
        return out

    def payoff_table(self, objectives: list[str]) -> dict | None:
        rows = {}
        for k in objectives:
            out = self.solve(f"payoff:{k}", _terms(k))
            if not out.ok:
                return None
            rows[k] = _obj_values(self.model)
        utopia, nadir = {}, {}
        for k in objectives:
            vals = [rows[r][k] for r in objectives]
            if SENSE[k] > 0:
                utopia[k], nadir[k] = rows[k][k], max(vals)
            else:
                utopia[k], nadir[k] = rows[k][k], min(vals)
        # raw values are used for bounds (rounding could make the utopia bound infeasible)
        return {"rows": {r: {k: round(v, 4) for k, v in vals.items()} for r, vals in rows.items()},
                "utopia": utopia, "nadir": nadir}


EPS_REL_TOL = 1e-7  # numerical tolerance added to epsilon bounds derived from solved values


def _epsilon_value(k: str, mode: str, value: float, payoff: dict | None) -> float:
    if mode == "absolute":
        return value
    u, n = payoff["utopia"][k], payoff["nadir"][k]
    eps = u + value * (n - u)
    return eps + SENSE[k] * EPS_REL_TOL * max(1.0, abs(eps))


def run_optimization(inp: SystemInputs, objective: ObjectiveConfig | None = None, diagnose: bool = True) -> dict:
    objective = objective or inp.scenario.objective
    started = datetime.now(timezone.utc)
    r = _Runner(inp)
    payoff = None
    final = None
    notes: list[str] = []

    if objective.method == "single":
        final = r.solve(f"single:{objective.primary}", _terms(objective.primary))

    elif objective.method == "weighted_sum":
        keys = [k for k, w in objective.weights.items() if w > 0]
        payoff = r.payoff_table(keys)
        if payoff is None:
            final = r.log and type("O", (), {"ok": False, "status": r.log[-1]["status"]})()
        else:
            total_w = sum(objective.weights[k] for k in keys)
            terms = {}
            for k in keys:
                rng = abs(payoff["nadir"][k] - payoff["utopia"][k])
                if rng < 1e-9:
                    notes.append(f"{k} does not conflict with the other objectives (utopia = nadir).")
                    rng = max(abs(payoff["utopia"][k]), 1.0)
                terms[k] = objective.weights[k] / total_w / rng
            final = r.solve("weighted_sum", terms)

    elif objective.method == "epsilon_constraint":
        need_payoff = any(b.mode == "fraction" for b in objective.epsilon_bounds.values())
        if need_payoff:
            payoff = r.payoff_table([objective.primary] + list(objective.epsilon_bounds))
        if need_payoff and payoff is None:
            final = type("O", (), {"ok": False, "status": r.log[-1]["status"]})()
        else:
            for k, b in objective.epsilon_bounds.items():
                eps = _epsilon_value(k, b.mode, b.value, payoff)
                add_bound(r.model, f"eps_{k}", k, eps)
                notes.append(f"epsilon bound: {k} {'>=' if SENSE[k] < 0 else '<='} {eps:,.3f} {OBJECTIVE_UNITS[k]}")
            final = r.solve(f"epsilon:{objective.primary}", _terms(objective.primary))

    elif objective.method == "lexicographic":
        tol = objective.lexicographic_tolerance_pct / 100.0
        for i, k in enumerate(objective.priorities):
            final = r.solve(f"lexicographic[{i + 1}]:{k}", _terms(k))
            if not final.ok:
                break
            if i < len(objective.priorities) - 1:
                best = float(pyo.value(r.model.obj_expr[k]))
                slack = tol * max(abs(best), 1e-6)
                bound = best + slack if SENSE[k] > 0 else best - slack
                add_bound(r.model, f"lex_{k}", k, bound)
                notes.append(f"stage {i + 1}: {k} fixed at {'<=' if SENSE[k] > 0 else '>='} {bound:,.3f} {OBJECTIVE_UNITS[k]}")

    base = {
        "method": objective.method,
        "objective_config": objective.model_dump(),
        "objective_description": OBJECTIVE_LABELS.get(objective.primary),
        "scenario": inp.scenario.model_dump(),
        "formulation": describe_formulation(inp),
        "data_status": inp.data_status,
        "provenance": inp.provenance,
        "assumptions": inp.assumptions,
        "demand_meta": inp.demand_meta,
        "baseline_2024": inp.baseline_metrics,
        "solver": {"name": r.solver_name, "model_build_seconds": round(r.build_seconds, 3), "solves": r.log},
        "payoff_table": payoff,
        "notes": notes,
        "started_at": started.isoformat(),
    }
    if final is None or not final.ok:
        status = getattr(final, "status", "error")
        base.update({"status": status, "feasible": False, "results": None, "objective_values": None,
                     "validation": None})
        if status == "infeasible" and diagnose:
            base["diagnostics"] = diagnose_infeasibility(inp)
            base["message"] = ("The optimization problem is infeasible: no capacity plan satisfies all "
                               "constraints. " + base["diagnostics"]["summary"])
        elif status == "error":
            base["message"] = "Solver error: " + "; ".join(x["message"] for x in r.log if x["message"]) \
                if any(x["message"] for x in r.log) else "Solver failed without a solution."
        else:
            base["message"] = f"Solver terminated with status '{status}'; no solution is reported."
        base["finished_at"] = datetime.now(timezone.utc).isoformat()
        return base

    new, gen, imp = _extract(inp, r.model)
    results = compute_indicators(inp, Dispatch(new_mw=new, gen_gwh=gen, imp_gwh=imp))
    validation = validate_solution(inp, r.model, new, gen, imp, results)
    base.update({
        "status": "optimal", "feasible": True, "results": results,
        "objective_values": results["objective_values"], "validation": validation,
        "message": "Optimal solution found and independently validated." if validation["status"] == "valid"
        else "Optimal solution found but validation reported issues (see validation).",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    return base


def pareto_frontier(inp: SystemInputs, primary: str = "min_cost", secondary: str = "min_emissions",
                    points: int = 6) -> dict:
    """Epsilon-constraint sweep: min primary s.t. secondary <= eps, eps from nadir to utopia."""
    if primary == secondary:
        raise ValueError("primary and secondary objectives must differ")
    points = max(3, min(points, 15))
    r = _Runner(inp)
    payoff = r.payoff_table([primary, secondary])
    if payoff is None:
        return {"status": r.log[-1]["status"], "points": [], "payoff_table": None,
                "message": "Problem infeasible: no Pareto frontier exists under the current constraints."}
    u, n = payoff["utopia"][secondary], payoff["nadir"][secondary]
    frontier = []
    for i in range(points):
        frac = 1 - i / (points - 1)
        eps = u + frac * (n - u)
        eps += SENSE[secondary] * EPS_REL_TOL * max(1.0, abs(eps))
        add_bound(r.model, "pareto", secondary, eps)
        out = r.solve(f"pareto[{i}]", _terms(primary))
        if not out.ok:
            frontier.append({"epsilon": eps, "status": out.status})
            continue
        vals = _obj_values(r.model)
        new, gen, imp = _extract(inp, r.model)
        res = compute_indicators(inp, Dispatch(new_mw=new, gen_gwh=gen, imp_gwh=imp))
        frontier.append({"epsilon": round(eps, 4), "status": "optimal",
                         "objective_values": res["objective_values"],
                         "renewable_share_final_year": res["summary"]["renewable_share_final_year"],
                         "total_investment_musd": res["summary"]["total_investment_musd"],
                         "total_new_capacity_mw": res["summary"]["total_new_capacity_mw"],
                         "solver_objective": round(vals[primary], 4)})
    return {"status": "optimal", "primary": primary, "secondary": secondary, "payoff_table": payoff,
            "points": frontier, "solver": {"name": r.solver_name, "solves": r.log},
            "data_status": inp.data_status}
