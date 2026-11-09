"""SQLAlchemy ORM models.

Persistence is independent from the optimization engine: the engine returns plain
dictionaries and this layer stores them. Works with SQLite (default) and PostgreSQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="USER")  # USER | ADMIN
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # rotated | logout
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    is_predefined: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(24), default="optimization")  # optimization | simulation | pareto
    scenario_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_name: Mapped[str] = mapped_column(String(160), default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    planning_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24))
    feasible: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(40), default="")
    data_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict] = mapped_column(JSON)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PlanningRequest(Base):
    __tablename__ = "planning_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="new")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    owner: Mapped[User] = relationship()


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    planning_request_id: Mapped[str] = mapped_column(ForeignKey("planning_requests.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("optimization_runs.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    strategic_objective: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # pending_review | approved | rejected | revision_requested | superseded | duplicate | infeasible
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    feasible: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class StrategyDecision(Base):
    """Append-only audit trail of administrator decisions."""
    __tablename__ = "strategy_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    strategy_version: Mapped[int] = mapped_column(Integer)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    admin_email: Mapped[str] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(32))  # approved | rejected | revision_requested | reference_superseded
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
