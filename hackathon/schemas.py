"""Strict Pydantic contracts for the complete approval path."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Transaction(StrictModel):
    transaction_id: Annotated[str, Field(min_length=1, max_length=100)]
    asset: Annotated[str, Field(min_length=1, max_length=32)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=8)]
    destination: Annotated[str, Field(min_length=1, max_length=200)]
    purpose: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("amount")
    @classmethod
    def reject_non_finite_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("amount must be finite")
        return value


class AgentFinding(StrictModel):
    agent: Literal["collector", "policy", "red-team"]
    verdict: Literal["allow", "review", "block"]
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    risks: tuple[str, ...] = ()


class Proposal(StrictModel):
    """The candidate action put forward by the Analyst."""

    proposal_id: Annotated[str, Field(min_length=1, max_length=100)]
    transaction_id: Annotated[str, Field(min_length=1, max_length=100)]
    content: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_refs: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


AgentRole = Literal["analyst", "skeptic", "safety", "arbiter"]
Verdict = Literal[
    "propose",
    "concur",
    "challenge",
    "pass",
    "fail",
    "recommend_approval",
    "recommend_rejection",
]
_ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "analyst": frozenset({"propose"}),
    "skeptic": frozenset({"concur", "challenge"}),
    "safety": frozenset({"pass", "fail"}),
    "arbiter": frozenset({"recommend_approval", "recommend_rejection"}),
}


class AgentVerdict(StrictModel):
    """One role's typed, hash-bound output. Every verdict names the proposal it saw."""

    agent_role: AgentRole
    proposal_sha256: Sha256
    verdict: Verdict
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]
    checks: tuple[str, ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def verdict_matches_role(self) -> AgentVerdict:
        if self.verdict not in _ALLOWED_VERDICTS[self.agent_role]:
            raise ValueError(f"verdict {self.verdict!r} is not valid for {self.agent_role}")
        return self


class ReviewPackage(StrictModel):
    schema_version: Literal["collaborative-review/v1"] = "collaborative-review/v1"
    transaction: Transaction
    findings: tuple[AgentFinding, ...]
    proposal: Proposal | None = None
    verdicts: tuple[AgentVerdict, ...] = ()
    human_approval_required: Literal[True] = True


class HumanApproval(StrictModel):
    schema_version: Literal["human-approval/v1"] = "human-approval/v1"
    review_sha256: Sha256
    decision: Literal["approved"] = "approved"
    approver: Annotated[str, Field(min_length=2, max_length=200)]
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at must include a timezone")
        return value


class EvidenceManifest(StrictModel):
    schema_version: Literal["evidence-manifest/v1"] = "evidence-manifest/v1"
    review: ReviewPackage
    review_sha256: Sha256
    approval: HumanApproval
    additional_approvals: tuple[HumanApproval, ...] = ()
    required_approvals: Annotated[int, Field(ge=1, le=10)] = 1

    @property
    def all_approvals(self) -> tuple[HumanApproval, ...]:
        return (self.approval, *self.additional_approvals)
