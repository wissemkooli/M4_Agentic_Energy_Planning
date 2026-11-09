"""The five predefined M4 scenarios.

Each scenario differs through actual parameters (demand, constraints, operating
assumptions, objective). The policy targets used here are ILLUSTRATIVE planning
assumptions for the prototype, not official Tunisian targets.
"""
from __future__ import annotations

from .schema import (
    ConstraintSet,
    DemandAssumptions,
    ObjectiveConfig,
    OperatingAssumptions,
    ScenarioConfig,
    TechnologyLimit,
    YearValue,
)


def predefined_scenarios() -> dict[str, ScenarioConfig]:
    return {
        "reference": ScenarioConfig(
            name="Reference",
            description=("Continuation of baseline assumptions: M1 point forecast, default reserve margin, "
                         "no policy targets, least-cost expansion."),
            demand=DemandAssumptions(basis="forecast"),
            objective=ObjectiveConfig(method="single", primary="min_cost"),
        ),
        "energy_efficiency": ScenarioConfig(
            name="Energy Efficiency",
            description=("Efficiency programmes reduce demand progressively, reaching 12 % below the M1 forecast "
                         "in 2040; least-cost expansion."),
            demand=DemandAssumptions(basis="forecast", efficiency_savings_pct_by_end=12.0),
            objective=ObjectiveConfig(method="single", primary="min_cost"),
        ),
        "accelerated_renewables": ScenarioConfig(
            name="Accelerated Renewables",
            description=("Faster solar and wind development: annual build limits raised to 900 MW (solar) and 450 MW (wind) and renewable share "
                         "targets of 35 % from 2030 and 50 % from 2035."),
            constraints=ConstraintSet(
                renewable_share_targets=[YearValue(year=2030, value=35), YearValue(year=2035, value=50)],
                technology_limits=[TechnologyLimit(technology="solar", max_annual_build_mw=900),
                                   TechnologyLimit(technology="wind", max_annual_build_mw=450)],
            ),
            objective=ObjectiveConfig(method="single", primary="min_cost"),
        ),
        "balanced_transition": ScenarioConfig(
            name="Balanced Transition",
            description=("Compromise between affordability, renewables and emissions: 30 % renewables from 2030, "
                         "emissions 15 % below 2024 from 2035, total budget 9,000 MUSD, weighted cost / emissions / "
                         "dependence objective."),
            constraints=ConstraintSet(
                budget_total_musd=9000, annual_budget_musd=1200,
                renewable_share_targets=[YearValue(year=2030, value=30)],
                emission_reduction_targets=[YearValue(year=2035, value=15)],
            ),
            objective=ObjectiveConfig(method="weighted_sum",
                                      weights={"min_cost": 0.5, "min_emissions": 0.3, "min_dependence": 0.2}),
        ),
        "stress": ScenarioConfig(
            name="Stress",
            description=("Demand on the M1 upper 80 % bound, renewable output 10 % lower, gas costs +30 %, imports "
                         "limited to 5 %, investment limited to 8,500 MUSD (700 MUSD per year)."),
            demand=DemandAssumptions(basis="upper_80"),
            constraints=ConstraintSet(budget_total_musd=8500, annual_budget_musd=700, max_import_share_pct=5),
            operating=OperatingAssumptions(renewable_availability_multiplier=0.9, gas_cost_multiplier=1.3),
            objective=ObjectiveConfig(method="single", primary="min_cost"),
        ),
    }
