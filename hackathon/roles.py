"""Deterministic Analyst -> Skeptic -> Safety -> Arbiter chain.

These are rule-based stand-ins with the same typed outputs an LLM-backed agent
must produce. Swap the body of a role for a Flower agent call and keep the
signature: input is a Transaction/Proposal, output is a validated AgentVerdict.
Every verdict carries the SHA-256 of the exact proposal it reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .canonical import sha256
from .schemas import AgentVerdict, Proposal, Transaction

# Strategic demo policy values, not a recommendation for real controls.
SAFETY_AMOUNT_LIMIT = Decimal("1000")
DENYLISTED_DESTINATIONS = frozenset({"attacker-wallet", "unknown"})


@dataclass(frozen=True)
class HoaPolicy:
    """Demo rules for an HOA vendor payment. Values are demo data, not real controls."""

    approved_vendors: frozenset[str]
    board_linked_vendors: frozenset[str]
    budget_line: str
    budget_remaining: Decimal
    reserve_balance: Decimal
    reserve_floor: Decimal


DEMO_HOA_POLICY = HoaPolicy(
    approved_vendors=frozenset(
        {
            "Blue Lagoon Pool Services",
            "GreenScape Landscaping",
            "Summit Roofing",
            "Harbor Repairs LLC",
        }
    ),
    board_linked_vendors=frozenset({"Harbor Repairs LLC"}),
    budget_line="Pool Maintenance",
    budget_remaining=Decimal("6000"),
    reserve_balance=Decimal("90000"),
    reserve_floor=Decimal("50000"),
)


def analyst_proposal(
    transaction: Transaction, created_at: datetime, evidence_refs: tuple[str, ...] = ()
) -> Proposal:
    return Proposal(
        proposal_id=f"prop-{transaction.transaction_id}",
        transaction_id=transaction.transaction_id,
        content=(
            f"Send {transaction.amount} {transaction.asset} to {transaction.destination} "
            f"for: {transaction.purpose}"
        ),
        evidence_refs=evidence_refs,
        created_at=created_at,
    )


def run_chain(
    transaction: Transaction,
    proposal: Proposal,
    created_at: datetime,
    policy: HoaPolicy | None = None,
) -> tuple[AgentVerdict, ...]:
    proposal_hash = sha256(proposal)

    analyst = AgentVerdict(
        agent_role="analyst",
        proposal_sha256=proposal_hash,
        verdict="propose",
        rationale="Candidate action stated with its supporting evidence references.",
        checks=(f"evidence_refs={len(proposal.evidence_refs)}",),
        created_at=created_at,
    )

    skeptic_issues = []
    if not proposal.evidence_refs:
        skeptic_issues.append("no evidence references supplied")
    if policy is not None:
        if transaction.amount > policy.budget_remaining:
            skeptic_issues.append(f"exceeds remaining '{policy.budget_line}' budget line")
        if policy.reserve_balance - transaction.amount < policy.reserve_floor:
            skeptic_issues.append("would take reserve funds below the required floor")
    skeptic = AgentVerdict(
        agent_role="skeptic",
        proposal_sha256=proposal_hash,
        verdict="challenge" if skeptic_issues else "concur",
        rationale="; ".join(skeptic_issues) or "No unsupported assumptions found.",
        checks=("evidence_present",),
        created_at=created_at,
    )

    safety_failures = []
    if policy is None:
        if transaction.amount > SAFETY_AMOUNT_LIMIT:
            safety_failures.append(f"amount exceeds {SAFETY_AMOUNT_LIMIT}")
        if transaction.destination in DENYLISTED_DESTINATIONS:
            safety_failures.append("destination is denylisted")
    else:
        if transaction.destination not in policy.approved_vendors:
            safety_failures.append("payee is not on the approved vendor list")
        if transaction.destination in policy.board_linked_vendors:
            safety_failures.append("payee is linked to a board member")
    safety = AgentVerdict(
        agent_role="safety",
        proposal_sha256=proposal_hash,
        verdict="fail" if safety_failures else "pass",
        rationale="; ".join(safety_failures) or "Policy checks passed.",
        checks=(
            ("approved_vendor_list", "board_link_check")
            if policy is not None
            else ("amount_limit", "destination_denylist")
        ),
        created_at=created_at,
    )

    reject = safety.verdict == "fail" or skeptic.verdict == "challenge"
    arbiter = AgentVerdict(
        agent_role="arbiter",
        proposal_sha256=proposal_hash,
        verdict="recommend_rejection" if reject else "recommend_approval",
        rationale=(
            "HOLD: blocking finding from Safety or unresolved Skeptic challenge."
            if reject
            else "PAY recommended: no blocking findings; the named board members decide."
        ),
        checks=("verdicts_consistent",),
        created_at=created_at,
    )
    return (analyst, skeptic, safety, arbiter)


def verdicts_consistent(verdicts: tuple[AgentVerdict, ...], proposal: Proposal) -> str:
    """Return an empty string if consistent, else a reason code."""
    proposal_hash = sha256(proposal)
    roles = [verdict.agent_role for verdict in verdicts]
    if roles != ["analyst", "skeptic", "safety", "arbiter"]:
        return "ROLE_SEQUENCE_INVALID"
    if any(verdict.proposal_sha256 != proposal_hash for verdict in verdicts):
        return "VERDICT_NOT_BOUND_TO_PROPOSAL"
    by_role = {verdict.agent_role: verdict.verdict for verdict in verdicts}
    blocked = by_role["safety"] == "fail" or by_role["skeptic"] == "challenge"
    if blocked and by_role["arbiter"] == "recommend_approval":
        return "ARBITER_OVERRODE_BLOCK"
    return ""
