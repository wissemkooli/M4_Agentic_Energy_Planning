"""History endpoint: scenarios, runs, planning requests and decisions of the current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...auth.deps import get_current_user
from ...db.models import OptimizationRun, PlanningRequest, Scenario, Strategy, StrategyDecision, User
from ...db.session import get_db
from ... import services

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
def history(user: User = Depends(get_current_user), db: Session = Depends(get_db),
            limit: int = Query(100, ge=1, le=500)):
    admin = user.role == "ADMIN"
    runs = select(OptimizationRun).order_by(OptimizationRun.created_at.desc()).limit(limit)
    reqs = select(PlanningRequest).order_by(PlanningRequest.updated_at.desc()).limit(limit)
    scen = select(Scenario).where(Scenario.is_predefined.is_(False)).order_by(Scenario.created_at.desc()).limit(limit)
    dec = (select(StrategyDecision, Strategy).join(Strategy, Strategy.id == StrategyDecision.strategy_id)
           .order_by(StrategyDecision.created_at.desc()).limit(limit))
    if not admin:
        runs = runs.where(OptimizationRun.owner_id == user.id)
        reqs = reqs.where(PlanningRequest.owner_id == user.id)
        scen = scen.where(Scenario.owner_id == user.id)
        dec = dec.where(Strategy.owner_id == user.id)
    return {
        "runs": [services.run_summary(r) for r in db.scalars(runs)],
        "planning_requests": [services.request_dict(r, full=False) for r in db.scalars(reqs)],
        "scenarios": [services.scenario_dict(s) for s in db.scalars(scen)],
        "decisions": [services.decision_dict(d, s) for d, s in db.execute(dec).all()],
    }
