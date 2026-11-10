"""Short-term operational problem schema (validated with Pydantic).

An operational problem asks how to run the EXISTING fleet over a few months: which gas
units to keep in service, how much each technology produces, how much to import and,
if capacity is insufficient, how much demand cannot be served. No investment decision
is made (that is the long-term planning problem in `m4.scenarios`).

Conventions: shares and percentages in percent (0-100); energy in GWh; money in MUSD.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..scenarios.schema import MAX_END_YEAR, REFERENCE_YEAR, DemandBasis

OpObjective = Literal["min_operating_cost", "min_unserved", "min_gas", "min_emissions"]
OP_OBJECTIVE_LABELS = {
    "min_operating_cost": "Minimize operating cost: fuel and variable O&M, imports, units kept in service and "
                          "unserved energy valued at the value of lost load (MUSD)",
    "min_unserved": "Minimize unserved energy, i.e. maximize demand satisfaction (GWh)",
    "min_gas": "Minimize gas-fired generation, i.e. fuel resource consumption (GWh)",
    "min_emissions": "Minimize CO2 emissions (MtCO2)",
}
OP_OBJECTIVE_UNITS = {"min_operating_cost": "MUSD", "min_unserved": "GWh", "min_gas": "GWh", "min_emissions": "MtCO2"}
MONTH_RE = re.compile(r"^(20\d\d)-(0[1-9]|1[0-2])$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapacityOutage(_Base):
    technology: str
    unavailable_mw: float = Field(..., ge=0, description="Installed capacity out of service during the period")


class OpObjectiveConfig(_Base):
    method: Literal["single", "lexicographic", "epsilon_constraint"] = "single"
    primary: OpObjective = "min_operating_cost"
    priorities: list[OpObjective] = Field(default_factory=list)
    lexicographic_tolerance_pct: float = Field(1.0, ge=0, le=50)
    epsilon_bounds: dict[OpObjective, float] = Field(
        default_factory=dict, description="Absolute upper bounds on other objectives (objective units)")

    @model_validator(mode="after")
    def _check(self) -> "OpObjectiveConfig":
        if self.method == "lexicographic" and (len(self.priorities) < 2 or len(set(self.priorities)) != len(self.priorities)):
            raise ValueError("lexicographic requires at least two distinct prioritized objectives")
        if self.method == "epsilon_constraint":
            if not self.epsilon_bounds:
                raise ValueError("epsilon_constraint requires at least one bound")
            if self.primary in self.epsilon_bounds:
                raise ValueError("the primary objective cannot also be bounded")
        return self


class OperationalProblem(_Base):
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field("", max_length=2000)
    start_month: str = Field(..., description="First month of the operating period, YYYY-MM")
    months: int = Field(1, ge=1, le=24)
    demand_basis: DemandBasis = Field("forecast", description="M1 column used for monthly demand")
    demand_change_pct: float = Field(0.0, ge=-50, le=100,
                                     description="Additional change applied to the M1 demand (stress test)")
    capacity_outages: list[CapacityOutage] = Field(default_factory=list)
    gas_generation_limit_gwh: float | None = Field(None, ge=0, description="Fuel availability limit over the period")
    max_import_share_pct: float | None = Field(None, ge=0, le=100, description="Monthly import limit (% of demand)")
    operating_reserve_pct: float | None = Field(None, ge=0, le=100)
    reserve_demand_basis: DemandBasis | None = Field(
        None, description="M1 column used to size the reserve (default: same as demand_basis)")
    allow_unserved_energy: bool = True
    value_of_lost_load_usd_per_mwh: float | None = Field(None, gt=0)
    unit_commitment: bool = Field(True, description="Integer number of gas units in service (MILP); False = LP relaxation")
    allow_imports: bool = True
    objective: OpObjectiveConfig = Field(default_factory=OpObjectiveConfig)

    @field_validator("start_month")
    @classmethod
    def _month(cls, v: str) -> str:
        m = MONTH_RE.match(v.strip())
        if not m:
            raise ValueError("start_month must have the format YYYY-MM")
        if not REFERENCE_YEAR < int(m.group(1)) <= MAX_END_YEAR:
            raise ValueError(f"start_month must be after the reference year {REFERENCE_YEAR}")
        return v.strip()

    @property
    def periods(self) -> list[tuple[int, int]]:
        y, m = int(self.start_month[:4]), int(self.start_month[5:])
        out = []
        for _ in range(self.months):
            out.append((y, m))
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return out

    @model_validator(mode="after")
    def _horizon(self) -> "OperationalProblem":
        if self.periods[-1][0] > MAX_END_YEAR:
            raise ValueError(f"operating period must end before {MAX_END_YEAR + 1}")
        techs = [o.technology for o in self.capacity_outages]
        if len(techs) != len(set(techs)):
            raise ValueError("one capacity outage entry per technology")
        return self


def period_label(p: tuple[int, int]) -> str:
    return f"{p[0]}-{p[1]:02d}"
