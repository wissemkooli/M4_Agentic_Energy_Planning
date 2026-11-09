"""Deterministic monthly energy-system simulation (merit-order dispatch).

The simulation answers "what happens with a GIVEN capacity plan?" without optimizing
investments. It is used to:
- assess the existing fleet (no new investment) against future demand,
- simulate a user-defined capacity expansion pathway,
- independently re-check optimization results (adequacy of the optimized fleet).

Dispatch rule for each month (y, m), with requirement R = demand x (1 + losses):
1. Resources are sorted by variable cost (USD/MWh); imports are inserted at their cost.
2. Each resource supplies min(remaining R, its potential):
      potential_t = installed_t x factor_t(m) x hours(y, m) / 1000   [GWh]
   factor = availability (dispatchable) or net capacity factor (variable / energy-limited).
3. Variable renewables together cannot exceed max_vre_share x R (flexibility proxy).
4. Imports share an annual allowance (max_import_share x annual supply) consumed
   chronologically, consistent with the annual import constraint of the optimizer.
5. Any remaining requirement is reported as unserved energy (never hidden).
Curtailment = variable potential - variable generation used.
"""
from __future__ import annotations

from .indicators import Dispatch, capacity_by_year, compute_indicators
from .system import SystemInputs


def simulate(inputs: SystemInputs, new_mw: dict[tuple[str, int], float] | None = None) -> dict:
    new_mw = new_mw or {}
    for (t, y), v in new_mw.items():
        if t not in inputs.techs or y not in inputs.years:
            raise ValueError(f"capacity plan entry ({t}, {y}) is outside the scenario")
        if v < 0:
            raise ValueError("capacity additions must be non-negative")
    cap = capacity_by_year(inputs, new_mw)
    order = sorted(list(inputs.techs) + (["__imports__"] if inputs.import_enabled else []),
                   key=lambda r: (inputs.import_cost if r == "__imports__" else inputs.techs[r].variable_cost,
                                  0 if r != "__imports__" and inputs.techs[r].category == "variable" else 1))
    gen, imp, uns = {}, {}, {}
    for y in inputs.years:
        import_room = inputs.max_import_share * inputs.annual_supply(y)
        for m in inputs.months:
            R = inputs.supply_req_gwh[(y, m)]
            remaining = R
            vre_room = inputs.max_vre_share * R
            for r in order:
                if r == "__imports__":
                    q = min(remaining, import_room)
                    import_room -= q
                    imp[(y, m)] = q
                else:
                    pot = inputs.potential_gwh(r, y, m, cap[(r, y)])
                    if inputs.techs[r].category == "variable":
                        pot = min(pot, vre_room)
                    q = min(remaining, pot)
                    if inputs.techs[r].category == "variable":
                        vre_room -= q
                    gen[(r, y, m)] = q
                remaining -= q
            uns[(y, m)] = max(0.0, remaining)
    result = compute_indicators(inputs, Dispatch(new_mw=dict(new_mw), gen_gwh=gen, imp_gwh=imp, unserved_gwh=uns))
    adequacy = []
    for a in result["annual"]:
        if a["unserved_gwh"] > 1e-3:
            adequacy.append(f"{a['year']}: {a['unserved_gwh']:.1f} GWh of demand cannot be supplied")
        if a["reserve_margin_achieved"] < inputs.reserve_margin - 1e-6:
            adequacy.append(f"{a['year']}: reserve margin {a['reserve_margin_achieved'] * 100:.1f} % "
                            f"< required {inputs.reserve_margin * 100:.1f} %")
    result["adequacy_issues"] = adequacy
    result["adequate"] = not adequacy
    result["method"] = "merit_order_simulation"
    return result
