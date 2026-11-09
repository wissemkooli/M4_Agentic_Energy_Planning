"""Scenario management and optimization/simulation endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...auth.deps import can_access, get_current_user
from ...data_integration.repository import get_inputs
from ...db.models import OptimizationRun, Scenario, User
from ...db.session import get_db
from ...modeling.system import build_system_inputs
from ...optimization.formulation import describe_formulation
from ...scenarios.schema import OBJECTIVE_LABELS, ScenarioConfig
from ... import services
from ..schemas import CompareRequest, OptimizationRequest, ParetoRequest, ScenarioCreate, SimulationRequest

router = APIRouter(prefix="/api", tags=["scenarios & optimization"])


@router.get("/scenarios")
def list_scenarios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Scenario).order_by(Scenario.is_predefined.desc(), Scenario.created_at.desc())
    if user.role != "ADMIN":
        q = q.where(or_(Scenario.owner_id.is_(None), Scenario.owner_id == user.id))
    return [services.scenario_dict(s) for s in db.scalars(q)]


@router.post("/scenarios", status_code=status.HTTP_201_CREATED)
def create_scenario(body: ScenarioCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.scenario_dict(services.create_scenario(db, user, body.config))


@router.post("/scenarios/validate")
def validate_scenario(body: ScenarioCreate, user: User = Depends(get_current_user)):
    """Validate a scenario against the schema AND the available data; returns its formulation."""
    inp = build_system_inputs(get_inputs(), body.config)
    return {"valid": True, "formulation": describe_formulation(inp), "demand_meta": inp.demand_meta}


@router.get("/scenarios/meta")
def scenario_meta(user: User = Depends(get_current_user)):
    b = get_inputs()
    return {"objectives": OBJECTIVE_LABELS, "technologies": list(b.assumptions.technologies["technology"]),
            "demand_bases": ["forecast", "lower_80", "upper_80", "lower_95", "upper_95"],
            "methods": ["single", "weighted_sum", "epsilon_constraint", "lexicographic"],
            "reference_year": 2024, "default_horizon": [2025, 2040],
            "m1_forecast_years": b.demand.forecast_years(), "has_intervals": b.demand.has_intervals,
            "defaults": ScenarioConfig(name="Custom scenario").model_dump(mode="json")}


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(Scenario, scenario_id)
    if s is None or not can_access(user, s.owner_id):
        raise HTTPException(404, "Scenario not found.")
    return services.scenario_dict(s)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(Scenario, scenario_id)
    if s is None or not can_access(user, s.owner_id):
        raise HTTPException(404, "Scenario not found.")
    if s.is_predefined:
        raise HTTPException(403, "Predefined scenarios cannot be deleted.")
    db.delete(s)
    db.commit()


@router.post("/optimization/run", status_code=status.HTTP_201_CREATED)
def run_optimization(body: OptimizationRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cfg, sid = services.resolve_scenario(db, user, body.scenario_id, body.scenario)
    run = services.optimize(db, user, cfg, sid, body.objective)
    return {**services.run_summary(run), "result": run.result}


@router.post("/simulation/run", status_code=status.HTTP_201_CREATED)
def run_simulation(body: SimulationRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cfg, sid = services.resolve_scenario(db, user, body.scenario_id, body.scenario)
    run = services.simulate_scenario(db, user, cfg, sid, body.capacity_plan)
    return {**services.run_summary(run), "result": run.result}


@router.post("/optimization/pareto", status_code=status.HTTP_201_CREATED)
def run_pareto(body: ParetoRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.primary == body.secondary:
        raise HTTPException(422, "primary and secondary objectives must differ")
    cfg, sid = services.resolve_scenario(db, user, body.scenario_id, body.scenario)
    run = services.pareto(db, user, cfg, sid, body.primary, body.secondary, body.points)
    return {**services.run_summary(run), "result": run.result}


@router.get("/optimization")
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db),
              kind: str | None = None, limit: int = Query(50, ge=1, le=200)):
    q = select(OptimizationRun).order_by(OptimizationRun.created_at.desc()).limit(limit)
    if user.role != "ADMIN":
        q = q.where(OptimizationRun.owner_id == user.id)
    if kind:
        q = q.where(OptimizationRun.kind == kind)
    return [services.run_summary(r) for r in db.scalars(q)]


@router.get("/optimization/{run_id}")
def get_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(OptimizationRun, run_id)
    if r is None or not can_access(user, r.owner_id):
        raise HTTPException(404, "Run not found.")
    return {**services.run_summary(r), "result": r.result}


@router.post("/comparison")
def compare(body: CompareRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    runs = []
    for rid in body.run_ids:
        r = db.get(OptimizationRun, rid)
        if r is None or not can_access(user, r.owner_id):
            raise HTTPException(404, f"Run {rid} not found.")
        runs.append(r)
    return services.compare_runs(runs)
