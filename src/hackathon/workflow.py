"""Human-gated core workflow and deterministic evidence manifest."""

from __future__ import annotations

from datetime import datetime

from .canonical import sha256
from .orchestration import deterministic_review
from .schemas import EvidenceManifest, HumanApproval, ReviewPackage, Transaction


class ApprovalError(RuntimeError):
    pass


def prepare(transaction: Transaction) -> tuple[ReviewPackage, str]:
    review = ReviewPackage(transaction=transaction, findings=deterministic_review(transaction))
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


def finalize(review: ReviewPackage, approval: HumanApproval) -> tuple[EvidenceManifest, str]:
    observed = sha256(review)
    if approval.review_sha256 != observed:
        raise ApprovalError("approval is not bound to this review package")
    manifest = EvidenceManifest(review=review, review_sha256=observed, approval=approval)
    return manifest, sha256(manifest)


def verify(manifest: EvidenceManifest, expected_manifest_sha256: str) -> None:
    if manifest.review_sha256 != sha256(manifest.review):
        raise ApprovalError("review content does not match the approved digest")
    if manifest.approval.review_sha256 != manifest.review_sha256:
        raise ApprovalError("approval digest does not match the manifest")
    if sha256(manifest) != expected_manifest_sha256:
        raise ApprovalError("evidence manifest SHA-256 mismatch")
