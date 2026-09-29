from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hackathon.bundle import (
    DIGEST_FILE,
    MANIFEST_FILE,
    build_demo,
    reset_demo,
    verify_bundle,
    write_bundle,
)
from hackathon.canonical import sha256
from hackathon.schemas import Transaction
from hackathon.workflow import ApprovalError, approve, finalize, prepare


def _tx() -> Transaction:
    return Transaction(
        transaction_id="t-1",
        asset="USDC",
        amount=Decimal("5.00"),
        destination="m",
        purpose="p",
    )


def _bundle(tmp_path: Path) -> Path:
    review, h = prepare(_tx())
    approval = approve(review, h, "Human Reviewer", datetime(2026, 1, 1, tzinfo=UTC))
    manifest, mh = finalize(review, approval)
    write_bundle(tmp_path / "b", manifest, mh)
    return tmp_path / "b"


def _edit(path: Path, fn) -> None:
    data = json.loads((path / MANIFEST_FILE).read_text())
    fn(data)
    (path / MANIFEST_FILE).write_text(json.dumps(data))


def test_golden_bundle_passes(tmp_path: Path) -> None:
    assert verify_bundle(_bundle(tmp_path)).passed


def test_demo_golden_passes_and_tampered_fails(tmp_path: Path) -> None:
    demo = build_demo(tmp_path / "demo")
    golden, tampered = demo["golden"], demo["tampered"]
    assert verify_bundle(golden).passed
    result = verify_bundle(tampered)
    assert not result.passed
    assert "MANIFEST_DIGEST_MISMATCH" in result.render() or "REVIEW_HASH" in result.render()


def test_one_field_change_fails(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _edit(b, lambda d: d["review"]["transaction"].update(purpose="changed"))
    assert not verify_bundle(b).passed


def test_key_order_does_not_change_verdict(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    data = json.loads((b / MANIFEST_FILE).read_text())
    (b / MANIFEST_FILE).write_text(json.dumps(dict(reversed(list(data.items())))))
    assert verify_bundle(b).passed


def test_substituted_review_is_detected(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    other, _ = prepare(_tx().model_copy(update={"destination": "other"}))
    replacement = other.model_dump(mode="json", by_alias=True)
    _edit(b, lambda d: d.update(review=replacement))
    assert not verify_bundle(b).passed


def test_swapped_digest_file_fails(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    (b / DIGEST_FILE).write_text("0" * 64 + "\n")
    assert not verify_bundle(b).passed


def test_missing_files_hard_fail(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    (b / MANIFEST_FILE).unlink()
    assert "MISSING_MANIFEST" in verify_bundle(b).render()
    b2 = _bundle(tmp_path / "x")
    (b2 / DIGEST_FILE).unlink()
    assert "MISSING_DIGEST" in verify_bundle(b2).render()


def test_extra_field_and_broken_json_fail(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _edit(b, lambda d: d.update(injected=True))
    assert "SCHEMA_INVALID" in verify_bundle(b).render()
    (b / MANIFEST_FILE).write_text("{not json")
    assert "SCHEMA_INVALID" in verify_bundle(b).render()


def test_approval_cannot_precede_or_skip_hash_binding() -> None:
    review, _ = prepare(_tx())
    with pytest.raises(ApprovalError):
        approve(review, sha256(review)[::-1], "Human Reviewer", datetime(2026, 1, 1, tzinfo=UTC))


def test_reset_only_removes_demo_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        reset_demo(tmp_path / "important")
    build_demo(tmp_path / "demo")
    reset_demo(tmp_path / "demo")
    assert not (tmp_path / "demo").exists()
