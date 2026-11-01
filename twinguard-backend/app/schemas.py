"""API contract. `report` is the core DecisionReport dumped unchanged."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Status = Literal[
    "running", "decided", "needs_review", "approved", "rejected", "applied",
    "failed", "needs_clarification", "stale_decision",
]
Outcome = Literal["approve", "block", "human_review"]


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Literal["planner", "manager", "viewer"]


class RequestIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class RequestCreated(BaseModel):
    request_id: str


class AgentStep(BaseModel):
    index: int
    tool: str
    summary: str


class RequestOut(BaseModel):
    request_id: str
    status: Status
    text: str
    created_by: str
    created_at: str
    steps: list[AgentStep] = []
    proposed_action: Optional[dict] = None
    decision: Optional[Outcome] = None
    report: Optional[dict] = None
    explanation: Optional[str] = None
    clarification: Optional[str] = None
    error: Optional[str] = None


class ReviewIn(BaseModel):
    comment: str = Field(min_length=1)


class ReviewTaskOut(BaseModel):
    request_id: str
    status: Literal["pending", "approved", "rejected"]
    action: dict
    reasons: list[str]
    created_at: str


class AuditEventOut(BaseModel):
    id: str
    request_id: Optional[str] = None
    kind: str
    actor: str
    at: str
    payload: dict = {}


class StateSummary(BaseModel):
    stock_by_warehouse: dict[str, int]
    capacity_use: dict[str, float]
    freshness: str
    findings: list[dict]


class ScenarioOut(BaseModel):
    id: str
    title: str
    synthetic: bool = True


class ScenarioRunOut(BaseModel):
    report: dict
    conditions: dict[str, str]
    truth: Optional[dict] = None
