from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hackathon.canonical import sha256
from hackathon.config import SponsorConfig, status
from hackathon.schemas import Transaction
from hackathon.workflow import ApprovalError, approve, finalize, prepare, verify


def transaction() -> Transaction:
    return Transaction(
        transaction_id="tx-001",
        asset="USDC",
        amount=Decimal("25.50"),
        destination="merchant-7",
        purpose="Hackathon purchase",
    )


def test_end_to_end_core_path() -> None:
    review, review_hash = prepare(transaction())
    approval = approve(
        review,
        review_hash,
        "Human Reviewer",
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest, manifest_hash = finalize(review, approval)
    verify(manifest, manifest_hash)
    assert manifest.review_sha256 == review_hash
    assert len(manifest.review.findings) == 3


def test_review_and_manifest_hashes_are_deterministic() -> None:
    first, first_hash = prepare(transaction())
    second, second_hash = prepare(transaction())
    assert first == second
    assert first_hash == second_hash == sha256(first)


def test_wrong_human_confirmation_fails_closed() -> None:
    review, _ = prepare(transaction())
    with pytest.raises(ApprovalError, match="does not match"):
        approve(review, "0" * 64, "Human Reviewer", datetime(2026, 1, 1, tzinfo=UTC))


def test_changed_review_invalidates_approval() -> None:
    review, review_hash = prepare(transaction())
    approval = approve(
        review,
        review_hash,
        "Human Reviewer",
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    changed = review.model_copy(
        update={"transaction": transaction().model_copy(update={"destination": "attacker"})}
    )
    with pytest.raises(ApprovalError, match="not bound"):
        finalize(changed, approval)


def test_contracts_reject_unknown_or_coerced_fields() -> None:
    payload = transaction().model_dump()
    payload["amount"] = "25.50"
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        Transaction.model_validate(payload)


def test_sponsor_configuration_reports_presence_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPONSOR_API_KEY", "test-secret")
    monkeypatch.setenv("SPONSOR_BASE_URL", "https://sponsor.example/v1/")
    monkeypatch.setenv("SPONSOR_MODEL", "sponsor-model")
    assert all(status().values())
    config = SponsorConfig.from_environment()
    assert config.base_url == "https://sponsor.example/v1"
