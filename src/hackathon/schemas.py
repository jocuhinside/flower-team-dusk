"""Strict Pydantic contracts for the complete approval path."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ReviewPackage(StrictModel):
    schema_version: Literal["collaborative-review/v1"] = "collaborative-review/v1"
    transaction: Transaction
    findings: tuple[AgentFinding, ...]
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
