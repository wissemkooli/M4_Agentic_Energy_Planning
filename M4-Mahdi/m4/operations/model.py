"""Pyomo model of the short-term operational problem (mixed-integer linear program).

Sets
  P operating months, T technologies, D ⊂ T dispatchable technologies (gas, unit commitment).

Decision variables
  units[d, p] ∈ {0, ..., N_avail[d, p]}  integer  gas units kept in service in month p
  gen[t, p]   >= 0  GWh                          generation of technology t
  imp[p]      >= 0  GWh                          imports
  uns[p]      >= 0  GWh                          unserved energy (fixed to 0 if not allowed)

Constraints
  balance[p]   sum_t gen[t,p] + imp[p] + uns[p] = D[p]
  max_out[d,p] gen[d,p] <= units[d,p] x size x avail_d x h[p] / 1000
  min_out[d,p] gen[d,p] >= min_stable x units[d,p] x size x avail_d x h[p] / 1000
  potential    gen[t,p] <= cap[t,p] x factor_t(month) x h[p] / 1000          (t not in D)
  vre[p]       sum_{t variable} gen[t,p] <= vre_max x D[p]
  imports[p]   imp[p] <= import_max x D[p]
  reserve[p]   sum_d units x size x avail_d + sum_{t not in D} credit_t x cap[t,p] >= (1 + r) x peak[p]
  gas_limit    sum_p gen[gas,p] <= G                                        (if a fuel limit is set)

Objectives (all linear)
  min_operating_cost = sum vc_t gen/1000 + c_imp imp/1000 + c_fix units + VOLL uns/1000     [MUSD]
  min_unserved = sum uns [GWh];  min_gas = sum gen[gas] [GWh];  min_emissions = sum ef gen / 1000 [Mt]

With unit_commitment=False, units[d,p] is continuous (LP relaxation of the same model).
"""
from __future__ import annotations

import pyomo.environ as pyo

from .inputs import OperationalInputs

SENSE = {"min_operating_cost": 1, "min_unserved": 1, "min_gas": 1, "min_emissions": 1}
ELASTIC_WEIGHTS = {"reserve": 1.0, "gas_limit": 1.0, "unserved_energy": 10.0}


def build_op_model(inp: OperationalInputs, elastic: bool = False) -> pyo.ConcreteModel:
    m = pyo.ConcreteModel(name=f"M4_ops_{inp.problem.name}")
    P = list(range(len(inp.periods)))
    per = inp.periods
    D = inp.dispatchable
    ND = [t for t in inp.techs if t not in D]
    m.P = pyo.Set(initialize=P, ordered=True)
    m.T = pyo.Set(initialize=list(inp.techs), ordered=True)
    m.D = pyo.Set(initialize=D, ordered=True)
    m.slack_names = []

    domain = pyo.NonNegativeIntegers if inp.unit_commitment else pyo.NonNegativeReals
    m.units = pyo.Var(m.D, m.P, within=domain, bounds=lambda _, d, i: (0, inp.units_available[(d, per[i])]))
    m.gen = pyo.Var(m.T, m.P, within=pyo.NonNegativeReals)
    m.imp = pyo.Var(m.P, within=pyo.NonNegativeReals)
    m.uns = pyo.Var(m.P, within=pyo.NonNegativeReals)
    for i in P:
        if not inp.import_enabled:
            m.imp[i].fix(0.0)

    def slack(name, indexed=True):
        if not elastic:
            return lambda *a: 0.0
        var_name = f"s_{name}"
        v = pyo.Var(m.P, within=pyo.NonNegativeReals) if indexed else pyo.Var(within=pyo.NonNegativeReals)
        m.add_component(var_name, v)
        m.slack_names.append((name, var_name))
        return (lambda i: v[i]) if indexed else (lambda *a: v)

    s_uns = slack("unserved_energy")
    if not inp.allow_unserved:
        for i in P:
            m.uns[i].fix(0.0)
    m.balance = pyo.Constraint(m.P, rule=lambda _, i: sum(m.gen[t, i] for t in inp.techs) + m.imp[i] + m.uns[i]
                               + s_uns(i) == inp.demand_gwh[per[i]])
    m.max_out = pyo.Constraint(m.D, m.P, rule=lambda _, d, i: m.gen[d, i] <= m.units[d, i] * inp.unit_output_gwh(d, per[i]))
    if inp.unit_commitment:
        m.min_out = pyo.Constraint(m.D, m.P, rule=lambda _, d, i:
                                   m.gen[d, i] >= inp.min_stable * m.units[d, i] * inp.unit_output_gwh(d, per[i]))
    m.potential = pyo.Constraint(ND, m.P, rule=lambda _, t, i: m.gen[t, i] <= inp.potential_gwh(t, per[i]))
    vre = inp.variable_techs
    if vre:
        m.vre = pyo.Constraint(m.P, rule=lambda _, i: sum(m.gen[t, i] for t in vre)
                               <= inp.max_vre_share * inp.demand_gwh[per[i]])
    if inp.import_enabled:
        m.import_limit = pyo.Constraint(m.P, rule=lambda _, i: m.imp[i] <= inp.max_import_share * inp.demand_gwh[per[i]])
    s_res = slack("reserve")
    m.reserve = pyo.Constraint(m.P, rule=lambda _, i:
                               sum(m.units[d, i] * inp.unit_size_mw * inp.techs[d].availability for d in D)
                               + sum(inp.techs[t].capacity_credit * inp.capacity_mw[(t, per[i])] for t in ND)
                               + s_res(i) >= (1 + inp.reserve_margin) * inp.peak_mw[per[i]])
    gas = inp.gas_techs
    if inp.gas_limit_gwh is not None and gas:
        s_g = slack("gas_limit", indexed=False)
        m.gas_limit = pyo.Constraint(expr=sum(m.gen[t, i] for t in gas for i in P) <= inp.gas_limit_gwh + s_g())

    m.variable_cost = pyo.Expression(expr=sum(inp.techs[t].variable_cost * m.gen[t, i] for t in inp.techs for i in P) / 1000.0
                                     + inp.import_cost * sum(m.imp[i] for i in P) / 1000.0)
    m.fixed_cost = pyo.Expression(expr=inp.unit_fixed_cost * sum(m.units[d, i] for d in D for i in P))
    m.unserved_cost = pyo.Expression(expr=inp.voll * sum(m.uns[i] for i in P) / 1000.0)
    m.obj_expr = {
        "min_operating_cost": pyo.Expression(expr=m.variable_cost + m.fixed_cost + m.unserved_cost),
        "min_unserved": pyo.Expression(expr=sum(m.uns[i] for i in P)),
        "min_gas": pyo.Expression(expr=sum(m.gen[t, i] for t in gas for i in P)),
        "min_emissions": pyo.Expression(expr=sum(inp.techs[t].emission_factor * m.gen[t, i] for t in inp.techs for i in P) / 1000.0
                                        + inp.import_ef * sum(m.imp[i] for i in P) / 1000.0),
    }
    for k, e in m.obj_expr.items():
        m.add_component(f"objexpr_{k}", e)
    if elastic:
        pen = 0
        for name, var_name in m.slack_names:
            v = getattr(m, var_name)
            pen = pen + ELASTIC_WEIGHTS[name] * (sum(v[i] for i in v) if v.is_indexed() else v)
        m.elastic_penalty = pyo.Expression(expr=pen)
    return m


def set_objective(m: pyo.ConcreteModel, terms: dict[str, float]) -> None:
    if hasattr(m, "objective"):
        m.del_component("objective")
    m.objective = pyo.Objective(expr=sum(c * SENSE[k] * m.obj_expr[k] for k, c in terms.items()), sense=pyo.minimize)


def add_bound(m: pyo.ConcreteModel, name: str, objective: str, bound: float) -> None:
    comp = f"bound_{name}"
    if hasattr(m, comp):
        m.del_component(comp)
    m.add_component(comp, pyo.Constraint(expr=m.obj_expr[objective] <= bound))
