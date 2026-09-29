"""Human-gated core workflow and deterministic evidence manifest."""

from __future__ import annotations

from datetime import datetime

from .canonical import sha256
from .orchestration import deterministic_review
from .roles import HoaPolicy, analyst_proposal, run_chain
from .schemas import EvidenceManifest, HumanApproval, ReviewPackage, Transaction


class ApprovalError(RuntimeError):
    pass


def prepare(transaction: Transaction) -> tuple[ReviewPackage, str]:
    review = ReviewPackage(transaction=transaction, findings=deterministic_review(transaction))
    return review, sha256(review)


def prepare_chain(
    transaction: Transaction,
    created_at: datetime,
    evidence_refs: tuple[str, ...] = (),
    policy: HoaPolicy | None = None,
) -> tuple[ReviewPackage, str]:
    """Review through the typed Analyst/Skeptic/Safety/Arbiter chain."""
    proposal = analyst_proposal(transaction, created_at, evidence_refs)
    review = ReviewPackage(
        transaction=transaction,
        findings=deterministic_review(transaction),
        proposal=proposal,
        verdicts=run_chain(transaction, proposal, created_at, policy),
    )
    return review, sha256(review)


def approve(
    review: ReviewPackage,
    confirmed_sha256: str,
    approver: str,
    approved_at: datetime,
) -> HumanApproval:
    observed = sha256(review)
    if confirmed_sha256 != observed:
        raise ApprovalError("human confirmation does not match the exact review SHA-256")
    return HumanApproval(
        review_sha256=observed,
        approver=approver,
        approved_at=approved_at,
    )


def finalize(
    review: ReviewPackage,
    approval: HumanApproval,
    additional_approvals: tuple[HumanApproval, ...] = (),
    required_approvals: int = 1,
) -> tuple[EvidenceManifest, str]:
    observed = sha256(review)
    approvals = (approval, *additional_approvals)
    if any(a.review_sha256 != observed for a in approvals):
        raise ApprovalError("approval is not bound to this review package")
    if len({a.approver.strip().lower() for a in approvals}) < required_approvals:
        raise ApprovalError(f"need {required_approvals} distinct approvers")
    manifest = EvidenceManifest(
        review=review,
        review_sha256=observed,
        approval=approval,
        additional_approvals=additional_approvals,
        required_approvals=required_approvals,
    )
    return manifest, sha256(manifest)


def verify(manifest: EvidenceManifest, expected_manifest_sha256: str) -> None:
    if manifest.review_sha256 != sha256(manifest.review):
        raise ApprovalError("review content does not match the approved digest")
    if any(a.review_sha256 != manifest.review_sha256 for a in manifest.all_approvals):
        raise ApprovalError("approval digest does not match the manifest")
    if sha256(manifest) != expected_manifest_sha256:
        raise ApprovalError("evidence manifest SHA-256 mismatch")
