from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hackathon.canonical import sha256
from hackathon.config import ConfigurationError, SponsorConfig, status
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


def test_falls_back_to_flower_runtime_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SPONSOR_API_KEY", "SPONSOR_BASE_URL", "SPONSOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLWR_MODEL_API_KEY", "runtime-secret")
    monkeypatch.setenv("FLWR_MODEL_API_ENDPOINT", "https://runtime.example/v1/")
    monkeypatch.setenv("SPONSOR_MODEL", "some-model")
    config = SponsorConfig.from_environment()
    assert config.api_key == "runtime-secret" and config.base_url == "https://runtime.example/v1"
    assert all(status().values())


def test_sponsor_variables_take_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPONSOR_API_KEY", "sponsor")
    monkeypatch.setenv("FLWR_MODEL_API_KEY", "runtime")
    monkeypatch.setenv("SPONSOR_BASE_URL", "https://s.example/v1")
    monkeypatch.setenv("SPONSOR_MODEL", "m")
    assert SponsorConfig.from_environment().api_key == "sponsor"


def test_missing_model_is_named_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPONSOR_API_KEY", "k")
    monkeypatch.setenv("SPONSOR_BASE_URL", "https://s.example/v1")
    monkeypatch.delenv("SPONSOR_MODEL", raising=False)
    from hackathon.config import ConfigurationError

    with pytest.raises(ConfigurationError, match="SPONSOR_MODEL"):
        SponsorConfig.from_environment()


def test_runtime_variables_win_and_are_unmodified(monkeypatch):
    for name in (
        "SPONSOR_API_KEY",
        "SPONSOR_BASE_URL",
        "FLWR_MODEL_API_KEY",
        "FLWR_MODEL_API_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLWR_RUNTIME_BASE_URL", "http://runtime.internal/v1/")
    monkeypatch.setenv("FLWR_RUNTIME_API_KEY", "runtime-key")
    monkeypatch.setenv("AGENT_MODEL", "endeavor-test-id")
    monkeypatch.delenv("SPONSOR_MODEL", raising=False)
    config = SponsorConfig.from_environment()
    assert config.runtime is True
    assert config.base_url == "http://runtime.internal/v1/"  # not stripped
    assert config.model == "endeavor-test-id"
    assert all(status().values())


def test_agent_model_overrides_sponsor_model(monkeypatch):
    monkeypatch.delenv("FLWR_RUNTIME_BASE_URL", raising=False)
    monkeypatch.delenv("FLWR_RUNTIME_API_KEY", raising=False)
    monkeypatch.setenv("SPONSOR_API_KEY", "k")
    monkeypatch.setenv("SPONSOR_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("SPONSOR_MODEL", "nebius-model")
    monkeypatch.setenv("AGENT_MODEL", "endeavor-test-id")
    config = SponsorConfig.from_environment()
    assert config.model == "endeavor-test-id"
    assert config.runtime is False


def test_runtime_without_model_fails_closed(monkeypatch):
    monkeypatch.setenv("FLWR_RUNTIME_BASE_URL", "http://runtime.internal/v1")
    monkeypatch.setenv("FLWR_RUNTIME_API_KEY", "runtime-key")
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("SPONSOR_MODEL", raising=False)
    with pytest.raises(ConfigurationError, match="AGENT_MODEL"):
        SponsorConfig.from_environment()


def test_full_responses_endpoint_is_reduced_to_base_url(monkeypatch):
    monkeypatch.delenv("FLWR_RUNTIME_BASE_URL", raising=False)
    monkeypatch.delenv("FLWR_RUNTIME_API_KEY", raising=False)
    monkeypatch.delenv("SPONSOR_BASE_URL", raising=False)
    monkeypatch.setenv("FLWR_MODEL_API_ENDPOINT", "https://api.example.test/v1/responses")
    monkeypatch.setenv("SPONSOR_API_KEY", "k")
    monkeypatch.setenv("AGENT_MODEL", "m")
    assert SponsorConfig.from_environment().base_url == "https://api.example.test/v1"
