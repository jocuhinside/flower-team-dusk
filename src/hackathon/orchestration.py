"""Deterministic agent roles used by tests and the no-quota smoke path."""

from __future__ import annotations

from .schemas import AgentFinding, Transaction


def deterministic_review(transaction: Transaction) -> tuple[AgentFinding, ...]:
    collector = AgentFinding(
        agent="collector",
        verdict="allow",
        summary=(
            f"Collected transaction {transaction.transaction_id}: "
            f"{transaction.amount} {transaction.asset}."
        ),
    )
    policy = AgentFinding(
        agent="policy",
        verdict="review",
        summary="Require a named human to confirm the exact review SHA-256.",
        risks=("approval substitution", "unreviewed destination"),
    )
    red_team = AgentFinding(
        agent="red-team",
        verdict="review",
        summary="Recompute all hashes after serialization and reject changed content.",
        risks=("post-approval tampering", "replay"),
    )
    return (collector, policy, red_team)
