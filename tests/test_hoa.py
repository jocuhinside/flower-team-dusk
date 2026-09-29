from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hackathon import signing
from hackathon.bundle import MANIFEST_FILE, build_demo, verify_bundle
from hackathon.roles import DEMO_HOA_POLICY, verdicts_consistent
from hackathon.schemas import Transaction
from hackathon.ui import handle_approval
from hackathon.workflow import ApprovalError, approve, finalize, prepare_chain

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def tx(**over) -> Transaction:
    base = dict(
        transaction_id="hoa-1",
        asset="USD",
        amount=Decimal("4800"),
        destination="Blue Lagoon Pool Services",
        purpose="Pool repair",
    )
    return Transaction(**{**base, **over})


def chain(refs=("invoice:1",), **over):
    review, h = prepare_chain(tx(**over), NOW, refs, DEMO_HOA_POLICY)
    return review, h


def verdicts(review):
    return {v.agent_role: v for v in review.verdicts}


def test_clean_invoice_gets_pay_recommendation() -> None:
    review, _ = chain()
    v = verdicts(review)
    assert v["arbiter"].verdict == "recommend_approval" and "PAY" in v["arbiter"].rationale
    assert verdicts_consistent(review.verdicts, review.proposal) == ""


@pytest.mark.parametrize(
    ("over", "role", "expect"),
    [
        ({"destination": "Unknown LLC"}, "safety", "not on the approved vendor list"),
        ({"destination": "Harbor Repairs LLC"}, "safety", "linked to a board member"),
        ({"amount": Decimal("7000")}, "skeptic", "budget line"),
        ({"amount": Decimal("6001"), "destination": "Summit Roofing"}, "skeptic", "budget line"),
    ],
)
def test_policy_blocks_produce_hold(over, role, expect) -> None:
    review, _ = chain(**over)
    v = verdicts(review)
    assert expect in v[role].rationale
    assert v["arbiter"].verdict == "recommend_rejection" and "HOLD" in v["arbiter"].rationale


def test_reserve_floor_challenge() -> None:
    from dataclasses import replace

    policy = replace(DEMO_HOA_POLICY, reserve_balance=Decimal("52000"))
    review, _ = prepare_chain(tx(), NOW, ("invoice:1",), policy)
    assert "reserve" in verdicts(review)["skeptic"].rationale


def test_missing_invoice_evidence_is_challenged() -> None:
    review, _ = chain(refs=())
    assert verdicts(review)["skeptic"].verdict == "challenge"


def test_two_approvers_required_and_must_be_distinct() -> None:
    review, h = chain()
    a = approve(review, h, "Board Member A", NOW)
    b = approve(review, h, "Board Member B", NOW)
    manifest, _ = finalize(review, a, (b,), required_approvals=2)
    assert len(manifest.all_approvals) == 2
    with pytest.raises(ApprovalError, match="distinct"):
        finalize(review, a, (), required_approvals=2)
    with pytest.raises(ApprovalError, match="distinct"):
        finalize(review, a, (approve(review, h, " board member a ", NOW),), required_approvals=2)


def test_approval_bound_to_other_review_is_rejected() -> None:
    review, h = chain()
    other, oh = chain(destination="Unknown LLC")
    a = approve(review, h, "Board Member A", NOW)
    b = approve(other, oh, "Board Member B", NOW)
    with pytest.raises(ApprovalError, match="not bound"):
        finalize(review, a, (b,), required_approvals=2)


def test_ui_collects_two_approvals_before_writing_bundle(tmp_path: Path) -> None:
    priv, _ = signing.generate_keypair(tmp_path / "k")
    key = signing.load_private(priv)
    review, h = chain()
    out, state = tmp_path / "out", []
    ok, msg = handle_approval(review, {"sha": h, "name": "Board Member A"}, out, key, NOW, state, 2)
    assert not ok and "1 of 2" in msg and not out.exists()
    ok, msg = handle_approval(review, {"sha": h, "name": "board member a"}, out, key, NOW, state, 2)
    assert not ok and "already" in msg and not out.exists()
    wrong = {"sha": "0" * 64, "name": "Board Member B"}
    ok, msg = handle_approval(review, wrong, out, key, NOW, state, 2)
    assert not ok and not out.exists()
    ok, _ = handle_approval(review, {"sha": h, "name": "Board Member B"}, out, key, NOW, state, 2)
    assert ok and (out / MANIFEST_FILE).is_file()
    pub = signing.load_public(tmp_path / "k" / "signer.pub")
    assert verify_bundle(out, pub).passed


def test_hoa_demo_bundles(tmp_path: Path) -> None:
    demo = build_demo(tmp_path / "demo")
    pub = signing.load_public(demo["public_key"])
    assert verify_bundle(demo["golden"], pub).passed
    text = (demo["golden"] / MANIFEST_FILE).read_text()
    assert "Blue Lagoon Pool Services" in text and "Board Member B (demo)" in text
    for name in ("tampered", "tampered-rehashed"):
        result = verify_bundle(demo[name], pub)
        assert not result.passed, name
    assert "Unknown LLC" in (demo["tampered"] / MANIFEST_FILE).read_text()
    assert "SIGNED_PAYLOAD_MISMATCH" in verify_bundle(demo["tampered-rehashed"], pub).render()


def test_removing_an_approval_or_lowering_the_requirement_fails(tmp_path: Path) -> None:
    demo = build_demo(tmp_path / "demo")
    pub = signing.load_public(demo["public_key"])
    path = demo["golden"] / MANIFEST_FILE
    data = json.loads(path.read_text())
    data["additional_approvals"] = []
    path.write_text(json.dumps(data))
    result = verify_bundle(demo["golden"], pub)
    assert not result.passed and "NOT_ENOUGH_APPROVERS" in result.render()
