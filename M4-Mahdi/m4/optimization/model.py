"""Pyomo capacity-expansion and monthly dispatch model (linear program).

Sets
  T technologies, Y planning years, M = {1..12} months.

Decision variables (all continuous, >= 0)
  new[t, y]     MW    capacity commissioned at the start of year y
  gen[t, y, m]  GWh   electricity generated (used) by technology t in month m of year y
  imp[y, m]     GWh   electricity imports
  (elastic diagnostic mode only) slack variables on soft-able constraints

Derived expressions
  cap[t, y] = existing[t, y] + sum_{y' <= y, y - y' < life_t} new[t, y']          MW

Constraints
  balance[y,m]     sum_t gen + imp = R[y,m]                       (R = demand x (1+losses))
  output[t,y,m]    gen <= cap x factor_t(m) x hours(y,m) / 1000   (factor: availability or net CF)
  vre[y,m]         sum_{t variable} gen <= vre_max x R[y,m]
  imports[y]       sum_m imp <= import_max x sum_m R
  reserve[y]       sum_t credit_t x cap[t,y] >= (1 + margin) x peak[y]
  build limits     new[t,y] <= max_annual_build_t ; sum_y new[t,y] <= max_total_new_t
  budget           sum_{t,y} capex_t x new <= B ;  sum_t capex_t x new[t,y] <= B_y
  re_target[y]     sum_{t renewable} gen >= share_y x sum_m R
  gas_share[y]     sum_m gen[gas] <= gas_max_y x sum_m R
  emissions[y]     E[y] <= cap_y

Curtailment is implicit: gen <= potential, so curtailment = potential - gen >= 0.
"""
from __future__ import annotations

import pyomo.environ as pyo

from ..modeling.system import SystemInputs

# Weights used only in elastic (infeasibility diagnosis) mode, per unit of slack.
ELASTIC_WEIGHTS = {
    "unserved_energy": 10.0,      # per GWh
    "renewable_target": 1.0,      # per GWh of missing renewable generation
    "gas_share": 1.0,             # per GWh of excess gas generation
    "emission_cap": 1000.0,       # per MtCO2 above cap
    "import_share": 1.0,          # per GWh of excess imports
    "reserve_margin": 1.0,        # per MW of missing firm capacity
    "budget_total": 1.0,          # per MUSD above budget
    "annual_budget": 1.0,         # per MUSD above annual budget
    "min_build": 1.0,             # per MW below mandated minimum build
}


def build_model(inp: SystemInputs, elastic: bool = False) -> pyo.ConcreteModel:
    m = pyo.ConcreteModel(name=f"M4_{inp.scenario.name}")
    T, Y, M = list(inp.techs), inp.years, inp.months
    m.T = pyo.Set(initialize=T, ordered=True)
    m.Y = pyo.Set(initialize=Y, ordered=True)
    m.M = pyo.Set(initialize=M, ordered=True)
    m.elastic = elastic
    m.slack_names = []

    def new_bounds(_, t, y):
        tech = inp.techs[t]
        return (0.0, tech.max_annual_build if tech.allow_investment else 0.0)

    m.new = pyo.Var(m.T, m.Y, within=pyo.NonNegativeReals, bounds=new_bounds)
    m.gen = pyo.Var(m.T, m.Y, m.M, within=pyo.NonNegativeReals)
    m.imp = pyo.Var(m.Y, m.M, within=pyo.NonNegativeReals)
    if not inp.import_enabled:
        for y in Y:
            for mo in M:
                m.imp[y, mo].fix(0.0)

    def cap_rule(_, t, y):
        life = inp.techs[t].lifetime
        return inp.existing_mw[(t, y)] + sum(m.new[t, yy] for yy in Y if yy <= y and y - yy < life)

    m.cap = pyo.Expression(m.T, m.Y, rule=cap_rule)

    def slack(name, index=None):
        """Create a non-negative slack (elastic mode) or return 0."""
        if not elastic:
            return (lambda *a: 0.0)
        var_name = f"s_{name}"
        if index is None:
            setattr(m, var_name, pyo.Var(within=pyo.NonNegativeReals))
            m.slack_names.append((name, var_name))
            v = getattr(m, var_name)
            return lambda *a: v
        setattr(m, var_name, pyo.Var(index, within=pyo.NonNegativeReals))
        m.slack_names.append((name, var_name))
        v = getattr(m, var_name)
        return lambda *a: v[a] if len(a) > 1 else v[a[0]]

    R = inp.supply_req_gwh
    S = {y: inp.annual_supply(y) for y in Y}

    # --- energy balance -------------------------------------------------------
    s_uns = slack("unserved_energy", m.Y * m.M)
    m.balance = pyo.Constraint(m.Y, m.M, rule=lambda _, y, mo:
                               sum(m.gen[t, y, mo] for t in T) + m.imp[y, mo] + s_uns(y, mo) == R[(y, mo)])

    # --- technical output limits -----------------------------------------------
    m.output = pyo.Constraint(m.T, m.Y, m.M, rule=lambda _, t, y, mo:
                              m.gen[t, y, mo] <= m.cap[t, y] * inp.techs[t].max_output_factor(mo)
                              * inp.hours[(y, mo)] / 1000.0)

    vre = inp.variable_techs
    # technical proxy: kept hard even in elastic mode (relaxing it would hide a physical limit)
    if vre:
        m.vre = pyo.Constraint(m.Y, m.M, rule=lambda _, y, mo:
                               sum(m.gen[t, y, mo] for t in vre) <= inp.max_vre_share * R[(y, mo)])

    s_imp = slack("import_share", m.Y)
    if inp.import_enabled:
        m.import_limit = pyo.Constraint(m.Y, rule=lambda _, y:
                                        sum(m.imp[y, mo] for mo in M) <= inp.max_import_share * S[y] + s_imp(y))

    # --- reliability -------------------------------------------------------------
    s_res = slack("reserve_margin", m.Y)
    m.reserve = pyo.Constraint(m.Y, rule=lambda _, y:
                               sum(inp.techs[t].capacity_credit * m.cap[t, y] for t in T) + s_res(y)
                               >= (1 + inp.reserve_margin) * inp.peak_mw[y])

    # --- build limits ------------------------------------------------------------
    m.total_build = pyo.Constraint(m.T, rule=lambda _, t:
                                   sum(m.new[t, y] for y in Y) <= inp.techs[t].max_total_new)
    min_build_techs = [t for t in T if inp.techs[t].min_total_new > 0]
    if min_build_techs:
        s_mb = slack("min_build", pyo.Set(initialize=min_build_techs))
        m.min_build = pyo.Constraint(min_build_techs, rule=lambda _, t:
                                     sum(m.new[t, y] for y in Y) + s_mb(t) >= inp.techs[t].min_total_new)

    # --- budget ------------------------------------------------------------------
    m.investment = pyo.Expression(expr=sum(inp.techs[t].capex * m.new[t, y] for t in T for y in Y))
    if inp.budget_total is not None:
        s_b = slack("budget_total")
        m.budget = pyo.Constraint(expr=m.investment <= inp.budget_total + s_b())
    if inp.annual_budget is not None:
        s_ab = slack("annual_budget", m.Y)
        m.annual_budget = pyo.Constraint(m.Y, rule=lambda _, y:
                                         sum(inp.techs[t].capex * m.new[t, y] for t in T)
                                         <= inp.annual_budget + s_ab(y))

    # --- policy targets -------------------------------------------------------------
    re_t = inp.renewable_techs
    if inp.re_min_share:
        ys = list(inp.re_min_share)
        s_re = slack("renewable_target", pyo.Set(initialize=ys))
        m.re_target = pyo.Constraint(ys, rule=lambda _, y:
                                     sum(m.gen[t, y, mo] for t in re_t for mo in M) + s_re(y)
                                     >= inp.re_min_share[y] * S[y])
    gas_t = inp.gas_techs
    if inp.gas_max_share and gas_t:
        ys = list(inp.gas_max_share)
        s_g = slack("gas_share", pyo.Set(initialize=ys))
        m.gas_share = pyo.Constraint(ys, rule=lambda _, y:
                                     sum(m.gen[t, y, mo] for t in gas_t for mo in M)
                                     <= inp.gas_max_share[y] * S[y] + s_g(y))

    m.emissions = pyo.Expression(m.Y, rule=lambda _, y:
                                 sum(inp.techs[t].emission_factor * m.gen[t, y, mo] for t in T for mo in M) / 1000.0
                                 + inp.import_ef * sum(m.imp[y, mo] for mo in M) / 1000.0)
    if inp.emission_cap_mt:
        ys = list(inp.emission_cap_mt)
        s_e = slack("emission_cap", pyo.Set(initialize=ys))
        m.emission_cap = pyo.Constraint(ys, rule=lambda _, y: m.emissions[y] <= inp.emission_cap_mt[y] + s_e(y))

    # --- objective expressions (all objectives are linear) ----------------------------
    def annual_cost(y):
        ann_cap = sum(inp.crf(inp.techs[t]) * inp.techs[t].capex
                      * sum(m.new[t, yy] for yy in Y if yy <= y and y - yy < inp.techs[t].lifetime) for t in T)
        fom = sum(inp.techs[t].fixed_om * m.cap[t, y] for t in T)
        vom = sum(inp.techs[t].variable_cost * m.gen[t, y, mo] for t in T for mo in M) / 1000.0
        impc = inp.import_cost * sum(m.imp[y, mo] for mo in M) / 1000.0
        return ann_cap + fom + vom + impc

    m.obj_expr = {
        "min_cost": pyo.Expression(expr=sum(inp.discount_factor(y) * annual_cost(y) for y in Y)),
        "min_emissions": pyo.Expression(expr=sum(m.emissions[y] for y in Y)),
        "min_dependence": pyo.Expression(expr=sum(
            inp.gas_import_fraction * sum(m.gen[t, y, mo] for t in gas_t for mo in M)
            + sum(m.imp[y, mo] for mo in M) for y in Y)),
        "max_renewables": pyo.Expression(expr=sum(m.gen[t, y, mo] for t in re_t for y in Y for mo in M)),
        "min_investment": pyo.Expression(expr=m.investment),
    }
    for k, e in m.obj_expr.items():
        m.add_component(f"objexpr_{k}", e)

    if elastic:
        penalty = 0
        for name, var_name in m.slack_names:
            v = getattr(m, var_name)
            w = ELASTIC_WEIGHTS[name]
            penalty = penalty + w * (sum(v[i] for i in v) if v.is_indexed() else v)
        m.elastic_penalty = pyo.Expression(expr=penalty)
    return m


SENSE = {"min_cost": 1, "min_emissions": 1, "min_dependence": 1, "max_renewables": -1, "min_investment": 1}


def set_objective(m: pyo.ConcreteModel, terms: dict[str, float]) -> None:
    """Activate `minimize sum_k coef_k x sense_k x f_k`. Replaces any existing objective."""
    if hasattr(m, "objective"):
        m.del_component("objective")
    expr = sum(coef * SENSE[k] * m.obj_expr[k] for k, coef in terms.items())
    m.objective = pyo.Objective(expr=expr, sense=pyo.minimize)


def add_bound(m: pyo.ConcreteModel, name: str, objective: str, bound: float) -> None:
    """Add f_k <= bound (or f_k >= bound for maximized objectives)."""
    comp = f"bound_{name}"
    if hasattr(m, comp):
        m.del_component(comp)
    e = m.obj_expr[objective]
    c = pyo.Constraint(expr=(e >= bound) if SENSE[objective] < 0 else (e <= bound))
    m.add_component(comp, c)
