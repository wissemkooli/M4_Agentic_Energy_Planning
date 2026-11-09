"""Scenario configuration schema (validated with Pydantic).

A scenario fully describes one planning experiment: horizon, demand assumptions,
policy constraints, operating assumptions and the optimization objective. Scenario
configs are stored as JSON with every run so results are reproducible.

Conventions
- Shares and percentages are expressed in percent (0-100) in the scenario.
- Money: MUSD (millions of constant 2024 USD) for investments and budgets.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REFERENCE_YEAR = 2024
MAX_END_YEAR = 2060

ObjectiveName = Literal["min_cost", "min_emissions", "min_dependence", "max_renewables", "min_investment"]
OBJECTIVE_LABELS = {
    "min_cost": "Minimize total discounted system cost (NPV, MUSD)",
    "min_emissions": "Minimize cumulative CO2 emissions (MtCO2)",
    "min_dependence": "Minimize externally dependent supply: gas (x gas_import_fraction) + imports (GWh)",
    "max_renewables": "Maximize cumulative renewable generation (equivalent to renewable share, GWh)",
    "min_investment": "Minimize total overnight investment (MUSD)",
}
OBJECTIVE_UNITS = {"min_cost": "MUSD", "min_emissions": "MtCO2", "min_dependence": "GWh",
                   "max_renewables": "GWh", "min_investment": "MUSD"}
DemandBasis = Literal["forecast", "lower_80", "upper_80", "lower_95", "upper_95"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class YearValue(_Base):
    year: int = Field(..., ge=REFERENCE_YEAR + 1, le=MAX_END_YEAR)
    value: float


class DemandAssumptions(_Base):
    basis: DemandBasis = Field("forecast", description="M1 column used: point forecast or interval bound")
    extrapolation_growth_pct: float | None = Field(
        None, ge=-5, le=15,
        description="Annual growth beyond the last full M1 forecast year. None = CAGR of the last 3 M1 years")
    efficiency_savings_pct_by_end: float = Field(
        0.0, ge=0, le=50, description="Demand reduction reached linearly by the final year (efficiency programmes)")
    demand_multiplier: float = Field(1.0, gt=0.3, le=2.0, description="Uniform stress/sensitivity multiplier")


class TechnologyLimit(_Base):
    technology: str
    allow_investment: bool = True
    max_annual_build_mw: float | None = Field(None, ge=0)
    max_total_new_mw: float | None = Field(None, ge=0)
    min_total_new_mw: float | None = Field(None, ge=0)


class ConstraintSet(_Base):
    budget_total_musd: float | None = Field(None, ge=0, description="Cap on cumulative overnight investment")
    annual_budget_musd: float | None = Field(None, ge=0, description="Cap on overnight investment in any year")
    renewable_share_targets: list[YearValue] = Field(default_factory=list, description="Min RE share (%) from year on")
    emission_reduction_targets: list[YearValue] = Field(
        default_factory=list, description="Min reduction (%) of annual CO2 vs the 2024 reference, from year on")
    emission_caps_mt: list[YearValue] = Field(default_factory=list, description="Absolute annual CO2 cap (Mt)")
    max_gas_share: list[YearValue] = Field(default_factory=list, description="Max gas share of supply (%) from year on")
    max_import_share_pct: float | None = Field(None, ge=0, le=100)
    reserve_margin_pct: float | None = Field(None, ge=0, le=100)
    max_vre_share_monthly_pct: float | None = Field(None, ge=0, le=100)
    technology_limits: list[TechnologyLimit] = Field(default_factory=list)

    @field_validator("renewable_share_targets", "emission_reduction_targets", "max_gas_share")
    @classmethod
    def _pct(cls, v: list[YearValue]) -> list[YearValue]:
        for item in v:
            if not 0 <= item.value <= 100:
                raise ValueError(f"percentage for {item.year} must be within 0-100 (got {item.value})")
        return sorted(v, key=lambda x: x.year)

    @field_validator("emission_caps_mt")
    @classmethod
    def _caps(cls, v: list[YearValue]) -> list[YearValue]:
        for item in v:
            if item.value < 0:
                raise ValueError("emission caps must be non-negative")
        return sorted(v, key=lambda x: x.year)


class OperatingAssumptions(_Base):
    renewable_availability_multiplier: float = Field(
        1.0, gt=0.3, le=1.3, description="Scales solar/wind/hydro capacity factors (stress: < 1)")
    gas_cost_multiplier: float = Field(1.0, gt=0.2, le=5.0)
    capex_multipliers: dict[str, float] = Field(default_factory=dict)
    discount_rate: float | None = Field(None, ge=0, le=0.3)
    allow_imports: bool = True

    @field_validator("capex_multipliers")
    @classmethod
    def _cm(cls, v: dict[str, float]) -> dict[str, float]:
        for k, x in v.items():
            if not 0.2 <= x <= 5:
                raise ValueError(f"capex multiplier for {k} must be within 0.2-5")
        return v


class EpsilonBound(_Base):
    mode: Literal["absolute", "fraction"] = Field(
        "fraction", description="absolute: value in objective units; fraction: 0 = best (utopia), 1 = worst (nadir)")
    value: float


class ObjectiveConfig(_Base):
    method: Literal["single", "weighted_sum", "epsilon_constraint", "lexicographic"] = "single"
    primary: ObjectiveName = "min_cost"
    weights: dict[ObjectiveName, float] = Field(default_factory=dict)
    epsilon_bounds: dict[ObjectiveName, EpsilonBound] = Field(default_factory=dict)
    priorities: list[ObjectiveName] = Field(default_factory=list)
    lexicographic_tolerance_pct: float = Field(1.0, ge=0, le=50)

    @model_validator(mode="after")
    def _check(self) -> "ObjectiveConfig":
        if self.method == "weighted_sum":
            if len(self.weights) < 2:
                raise ValueError("weighted_sum requires weights for at least two objectives")
            if any(w < 0 for w in self.weights.values()) or sum(self.weights.values()) <= 0:
                raise ValueError("weights must be non-negative with a positive sum")
        if self.method == "epsilon_constraint":
            if not self.epsilon_bounds:
                raise ValueError("epsilon_constraint requires at least one epsilon bound")
            if self.primary in self.epsilon_bounds:
                raise ValueError("the primary objective cannot also be epsilon-bounded")
            for k, b in self.epsilon_bounds.items():
                if b.mode == "fraction" and not 0 <= b.value <= 1:
                    raise ValueError(f"fraction epsilon for {k} must be within 0-1")
        if self.method == "lexicographic":
            if len(self.priorities) < 2 or len(set(self.priorities)) != len(self.priorities):
                raise ValueError("lexicographic requires at least two distinct prioritized objectives")
        return self


class ScenarioConfig(_Base):
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field("", max_length=2000)
    start_year: int = Field(2025, ge=REFERENCE_YEAR + 1, le=MAX_END_YEAR)
    end_year: int = Field(2040, ge=REFERENCE_YEAR + 1, le=MAX_END_YEAR)
    demand: DemandAssumptions = Field(default_factory=DemandAssumptions)
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    operating: OperatingAssumptions = Field(default_factory=OperatingAssumptions)
    objective: ObjectiveConfig = Field(default_factory=ObjectiveConfig)

    @model_validator(mode="after")
    def _horizon(self) -> "ScenarioConfig":
        if self.end_year < self.start_year:
            raise ValueError("end_year must be greater than or equal to start_year")
        if self.end_year - self.start_year > 35:
            raise ValueError("planning horizon longer than 36 years is not supported")
        c = self.constraints
        for label, items in (("renewable target", c.renewable_share_targets),
                             ("emission reduction target", c.emission_reduction_targets),
                             ("emission cap", c.emission_caps_mt), ("gas share limit", c.max_gas_share)):
            for it in items:
                if not self.start_year <= it.year <= self.end_year:
                    raise ValueError(f"{label} year {it.year} is outside the horizon {self.start_year}-{self.end_year}")
        if c.annual_budget_musd is not None and c.budget_total_musd is not None \
                and c.annual_budget_musd > c.budget_total_musd:
            raise ValueError("annual budget cannot exceed the total budget")
        return self
