"""Administrator endpoints (role enforced on the backend with `require_admin`)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...auth.deps import require_admin
from ...auth.security import create_user
from ...db.models import PlanningRequest, Strategy, StrategyDecision, User
from ...db.session import get_db
from ... import services
from ..schemas import DecisionRequest, RejectRequest, RevisionRequest, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _strategy(db: Session, sid: str) -> Strategy:
    s = db.get(Strategy, sid)
    if s is None:
        raise HTTPException(404, "Strategy not found.")
    return s


@router.get("/overview")
def overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    recent = db.execute(select(StrategyDecision, Strategy).join(Strategy, Strategy.id == StrategyDecision.strategy_id)
                        .order_by(StrategyDecision.created_at.desc()).limit(8)).all()
    ref = db.scalar(select(Strategy).where(Strategy.is_reference.is_(True)))
    return {
        "pending_proposals": count(Strategy, Strategy.status == "pending_review"),
        "planning_requests": count(PlanningRequest),
        "approved_strategies": count(Strategy, Strategy.status == "approved"),
        "rejected_strategies": count(Strategy, Strategy.status == "rejected"),
        "revision_requests": count(Strategy, Strategy.status == "revision_requested"),
        "users": count(User),
        "recent_decisions": [services.decision_dict(d, s) for d, s in recent],
        "approved_reference_strategy": services.strategy_dict(ref) if ref else None,
    }


@router.get("/review-queue")
def review_queue(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Strategy).where(Strategy.status == "pending_review").order_by(Strategy.created_at))
    out = []
    for s in rows:
        d = services.strategy_dict(s)
        req = db.get(PlanningRequest, s.planning_request_id)
        d["request_title"] = req.title if req else None
        d["requester_email"] = req.owner.email if req and req.owner else None
        out.append(d)
    return out


@router.post("/strategies/{strategy_id}/approve")
def approve(strategy_id: str, body: DecisionRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return services.strategy_dict(services.decide(db, admin, _strategy(db, strategy_id), "approved", body.comment), full=True)


@router.post("/strategies/{strategy_id}/reject")
def reject(strategy_id: str, body: RejectRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return services.strategy_dict(services.decide(db, admin, _strategy(db, strategy_id), "rejected", body.reason), full=True)


@router.post("/strategies/{strategy_id}/request-revision")
def request_revision(strategy_id: str, body: RevisionRequest, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    return services.strategy_dict(services.decide(db, admin, _strategy(db, strategy_id), "revision_requested",
                                                  body.comment), full=True)


@router.get("/decisions")
def decisions(admin: User = Depends(require_admin), db: Session = Depends(get_db),
              limit: int = Query(200, ge=1, le=1000)):
    rows = db.execute(select(StrategyDecision, Strategy).join(Strategy, Strategy.id == StrategyDecision.strategy_id)
                      .order_by(StrategyDecision.created_at.desc()).limit(limit)).all()
    return [services.decision_dict(d, s) for d, s in rows]


@router.get("/users", response_model=list[UserOut])
def users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return create_user(db, body.email, body.username, body.password, body.role, body.full_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found.")
    if u.id == admin.id and (body.role == "USER" or body.is_active is False):
        raise HTTPException(409, "Administrators cannot demote or deactivate their own account.")
    if body.role is not None:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    db.commit()
    db.refresh(u)
    return u
