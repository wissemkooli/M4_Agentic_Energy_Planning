"""Agent (planning request) endpoints and strategy read endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...agent.graph import GRAPH_NODES
from ...agent.llm import LLMClient
from ...agent.tools import tool_catalog
from ...agent.usage import usage_summary
from ...operations.schema import OperationalProblem
from ...auth.deps import can_access, get_current_user
from ...db.models import OptimizationRun, PlanningRequest, Strategy, StrategyDecision, User
from ...db.session import get_db
from ... import services
from ..schemas import AgentMessageRequest, AgentStartRequest, ApprovalRequest, SelectionRequest

router = APIRouter(prefix="/api", tags=["agent & strategies"])


def _req(db: Session, user: User, request_id: str) -> PlanningRequest:
    r = db.get(PlanningRequest, request_id)
    if r is None or not can_access(user, r.owner_id):
        raise HTTPException(404, "Planning request not found.")
    return r


@router.get("/agent/info")
def agent_info(user: User = Depends(get_current_user)):
    c = LLMClient()
    usage = usage_summary(1)
    limit = c.s.llm_daily_call_limit
    return {"llm_configured": c.available, "llm_model": c.model if c.available else None,
            "llm_fallback_models": c.fallback_models if c.available else [],
            "mode": ("llm-assisted" if c.available else "deterministic (no LLM configured)")
            + (" — daily LLM budget reached, deterministic fallback" if c.available and limit and usage["calls"] >= limit else ""),
            "llm_usage_today": {**usage, "daily_call_limit": limit or None},
            "problem_types": {"planning": "long-term capacity expansion (LP)",
                              "operational": "short-term operation of the existing fleet (MILP, unit commitment)"},
            "workflow_nodes": GRAPH_NODES, "tools": tool_catalog(),
            "human_gates": ["clarification answers", "formulation approval (user)", "preferred option (user)",
                            "strategy decision (administrator)"]}


@router.post("/agent/requests", status_code=status.HTTP_201_CREATED)
def start(body: AgentStartRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.request_dict(services.start_request(db, user, body.message, body.title))


@router.get("/agent/requests")
def list_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                  limit: int = Query(50, ge=1, le=200)):
    q = select(PlanningRequest).order_by(PlanningRequest.updated_at.desc()).limit(limit)
    if user.role != "ADMIN":
        q = q.where(PlanningRequest.owner_id == user.id)
    return [services.request_dict(r, full=False) for r in db.scalars(q)]


@router.get("/agent/requests/{request_id}")
def get_request(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.request_dict(_req(db, user, request_id))


@router.get("/agent/requests/{request_id}/investigation")
def get_investigation(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (_req(db, user, request_id).state or {}).get("investigation")


@router.post("/agent/requests/{request_id}/message")
def message(request_id: str, body: AgentMessageRequest, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    if not (body.message and body.message.strip()) and not body.answers:
        raise HTTPException(422, "Provide a message or structured answers.")
    return services.request_dict(services.post_message(db, user, _req(db, user, request_id), body.message, body.answers))


@router.post("/agent/requests/{request_id}/approve")
def approve(request_id: str, body: ApprovalRequest, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    req = _req(db, user, request_id)
    if req.owner_id != user.id and user.role != "ADMIN":
        raise HTTPException(403, "Only the requester or an administrator can approve the formulation.")
    return services.request_dict(services.approve_formulation(db, user, req, body.approve, body.comment))


@router.post("/agent/requests/{request_id}/select")
def select_preferred(request_id: str, body: SelectionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    req = _req(db, user, request_id)
    if req.owner_id != user.id and user.role != "ADMIN":
        raise HTTPException(403, "Only the requester can record a preferred option.")
    return services.request_dict(services.select_strategy(db, user, req, body.strategy_id, body.comment))


@router.post("/operations/optimize", status_code=status.HTTP_201_CREATED, tags=["operations"])
def optimize_operations(body: OperationalProblem, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Solve one short-term operational problem (MILP) directly, without the agent."""
    run = services.optimize_operations(db, user, body)
    return {**services.run_summary(run), "result": run.result}


@router.get("/strategies")
def list_strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                    request_id: str | None = None, status_filter: str | None = Query(None, alias="status")):
    q = select(Strategy).order_by(Strategy.created_at.desc())
    if user.role != "ADMIN":
        q = q.where(Strategy.owner_id == user.id)
    if request_id:
        q = q.where(Strategy.planning_request_id == request_id)
    if status_filter:
        q = q.where(Strategy.status == status_filter)
    return [services.strategy_dict(s) for s in db.scalars(q)]


@router.get("/strategies/reference")
def reference_strategy(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.scalar(select(Strategy).where(Strategy.is_reference.is_(True)))
    return services.strategy_dict(s, full=True) if s else None


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(Strategy, strategy_id)
    if s is None or not can_access(user, s.owner_id):
        raise HTTPException(404, "Strategy not found.")
    d = services.strategy_dict(s, full=True)
    run = db.get(OptimizationRun, s.run_id) if s.run_id else None
    if run:
        res = run.result or {}
        d["run"] = {**services.run_summary(run), "validation": res.get("validation"), "solver": res.get("solver"),
                    "formulation": res.get("formulation"), "provenance": res.get("provenance"),
                    "objective_config": res.get("objective_config"), "notes": res.get("notes"),
                    "annual": (res.get("results") or {}).get("annual"),
                    "monthly": (res.get("results") or {}).get("monthly")}
    d["decisions"] = [services.decision_dict(x, s) for x in db.scalars(
        select(StrategyDecision).where(StrategyDecision.strategy_id == s.id).order_by(StrategyDecision.created_at))]
    siblings = db.scalars(select(Strategy).where(Strategy.planning_request_id == s.planning_request_id,
                                                 Strategy.key == s.key).order_by(Strategy.version))
    d["versions"] = [{"id": x.id, "version": x.version, "status": x.status} for x in siblings]
    return d
