"""Solver wrapper around Pyomo + HiGHS.

Returns a normalized status; never loads or returns a solution unless the solver
reports an optimal termination.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pyomo.environ as pyo
from pyomo.opt import TerminationCondition

from ..config import get_settings


class SolverUnavailableError(RuntimeError):
    pass


@dataclass
class SolveOutcome:
    status: str             # optimal | infeasible | unbounded | time_limit | error
    termination: str
    solver: str
    seconds: float
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


_STATUS = {
    TerminationCondition.optimal: "optimal",
    TerminationCondition.locallyOptimal: "optimal",
    TerminationCondition.globallyOptimal: "optimal",
    TerminationCondition.infeasible: "infeasible",
    TerminationCondition.infeasibleOrUnbounded: "infeasible",
    TerminationCondition.unbounded: "unbounded",
    TerminationCondition.maxTimeLimit: "time_limit",
}


def get_solver(name: str | None = None):
    name = name or get_settings().solver_name
    candidates = [name] + [n for n in ("appsi_highs", "highs") if n != name]
    for n in candidates:
        try:
            s = pyo.SolverFactory(n)
            if s is not None and s.available(exception_flag=False):
                return n, s
        except Exception:
            continue
    raise SolverUnavailableError(
        "No HiGHS solver available. Install it with: pip install highspy  (see requirements.txt)")


def solve(model: pyo.ConcreteModel, solver_name: str | None = None) -> SolveOutcome:
    t0 = time.perf_counter()
    try:
        name, solver = get_solver(solver_name)
    except SolverUnavailableError as exc:
        return SolveOutcome("error", "solver_unavailable", solver_name or "highs", 0.0, str(exc))
    try:
        if name == "appsi_highs":
            solver.config.time_limit = get_settings().solver_time_limit_seconds
        res = solver.solve(model, load_solutions=False)
    except Exception as exc:  # solver crash or interface error
        return SolveOutcome("error", "exception", name, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}")
    tc = res.solver.termination_condition
    status = _STATUS.get(tc, "error")
    if status == "optimal":
        model.solutions.load_from(res)
    return SolveOutcome(status, str(tc), name, time.perf_counter() - t0)
