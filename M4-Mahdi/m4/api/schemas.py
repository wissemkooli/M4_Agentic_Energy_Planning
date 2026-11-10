"""Pydantic request/response schemas of the REST API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..scenarios.schema import ObjectiveConfig, ObjectiveName, ScenarioConfig


class LoginRequest(BaseModel):
    login: str = Field(..., min_length=3, max_length=255, description="Email or username")
    password: str = Field(..., min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str
    full_name: str
    role: Literal["USER", "ADMIN"]
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = Field(None, description="Also set as an httpOnly cookie for browsers")
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(..., min_length=10, max_length=256)
    full_name: str = ""
    role: Literal["USER", "ADMIN"] = "USER"


class UserUpdate(BaseModel):
    role: Literal["USER", "ADMIN"] | None = None
    is_active: bool | None = None


class ScenarioCreate(BaseModel):
    config: ScenarioConfig


class OptimizationRequest(BaseModel):
    scenario_id: str | None = None
    scenario: ScenarioConfig | None = None
    objective: ObjectiveConfig | None = Field(None, description="Overrides the scenario objective")


class SimulationRequest(BaseModel):
    scenario_id: str | None = None
    scenario: ScenarioConfig | None = None
    capacity_plan: dict[str, dict[int, float]] | None = Field(
        None, description="Optional additions {technology: {year: MW}}; empty = existing fleet only")


class ParetoRequest(BaseModel):
    scenario_id: str | None = None
    scenario: ScenarioConfig | None = None
    primary: ObjectiveName = "min_cost"
    secondary: ObjectiveName = "min_emissions"
    points: int = Field(6, ge=3, le=15)


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(..., min_length=1, max_length=10)


class AgentStartRequest(BaseModel):
    message: str = Field(..., min_length=3, max_length=4000)
    title: str | None = Field(None, max_length=200)


class AgentMessageRequest(BaseModel):
    message: str | None = Field(None, max_length=4000)
    answers: dict[str, Any] | None = Field(None, description="Structured answers {question_id: value | 'accept'}")


class ApprovalRequest(BaseModel):
    approve: bool
    comment: str = Field("", max_length=2000)


class SelectionRequest(BaseModel):
    strategy_id: str
    comment: str = Field("", max_length=2000)


class DecisionRequest(BaseModel):
    comment: str = Field("", max_length=4000)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=4000)


class RevisionRequest(BaseModel):
    comment: str = Field(..., min_length=5, max_length=4000)


class Message(BaseModel):
    detail: str
