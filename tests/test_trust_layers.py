from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from hackathon import signing
from hackathon.bundle import DIGEST_FILE, MANIFEST_FILE, build_demo, verify_bundle, write_bundle
from hackathon.canonical import sha256
from hackathon.roles import verdicts_consistent
from hackathon.schemas import AgentVerdict, Transaction
from hackathon.ui import handle_approval, render_page
from hackathon.workflow import approve, finalize, prepare_chain

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def tx(**over) -> Transaction:
    base = dict(
        transaction_id="t-1", asset="USDC", amount=Decimal("5"), destination="m", purpose="p"
    )
    return Transaction(**{**base, **over})


def signed_bundle(tmp_path: Path):
    priv, pub = signing.generate_keypair(tmp_path / "k")
    review, h = prepare_chain(tx(), NOW, ("e1",))
    manifest, mh = finalize(review, approve(review, h, "Human Reviewer", NOW))
    write_bundle(tmp_path / "b", manifest, mh, signing.load_private(priv))
    return tmp_path / "b", signing.load_public(pub), manifest


def test_signed_bundle_verifies_with_trusted_key(tmp_path: Path) -> None:
    bundle, pub, _ = signed_bundle(tmp_path)
    result = verify_bundle(bundle, pub)
    assert result.passed and "signature valid" in result.render()


def test_wrong_key_fails(tmp_path: Path) -> None:
    bundle, _, _ = signed_bundle(tmp_path)
    _, other_pub = signing.generate_keypair(tmp_path / "other")
    result = verify_bundle(bundle, signing.load_public(other_pub))
    assert not result.passed and "SIGNATURE_INVALID" in result.render()


def test_stripped_signature_fails_when_key_supplied(tmp_path: Path) -> None:
    bundle, pub, _ = signed_bundle(tmp_path)
    (bundle / signing.ENVELOPE_FILE).unlink()
    assert "MISSING_SIGNATURE" in verify_bundle(bundle, pub).render()


def test_no_key_is_reported_not_silently_passed(tmp_path: Path) -> None:
    bundle, _, _ = signed_bundle(tmp_path)
    assert "signature not checked" in verify_bundle(bundle).render()


def test_rehashed_tamper_is_caught_only_by_signature(tmp_path: Path) -> None:
    demo = build_demo(tmp_path / "demo")
    pub = signing.load_public(demo["public_key"])
    forged = demo["tampered-rehashed"]
    assert verify_bundle(forged).passed  # digest alone is fooled
    result = verify_bundle(forged, pub)
    assert not result.passed
    assert "SIGNED_PAYLOAD_MISMATCH" in result.render() or "SIGNATURE_INVALID" in result.render()


def test_envelope_payload_swap_is_detected(tmp_path: Path) -> None:
    bundle, pub, manifest = signed_bundle(tmp_path)
    path = bundle / signing.ENVELOPE_FILE
    env = json.loads(path.read_text())
    env["payload"] = base64.b64encode(b"{}").decode()
    path.write_text(json.dumps(env))
    assert not verify_bundle(bundle, pub).passed


def test_keygen_never_overwrites(tmp_path: Path) -> None:
    signing.generate_keypair(tmp_path)
    with pytest.raises(FileExistsError):
        signing.generate_keypair(tmp_path)


def test_chain_is_bound_and_ordered() -> None:
    review, _ = prepare_chain(tx(), NOW, ("e1",))
    assert [v.agent_role for v in review.verdicts] == ["analyst", "skeptic", "safety", "arbiter"]
    assert verdicts_consistent(review.verdicts, review.proposal) == ""
    assert review.verdicts[-1].verdict == "recommend_approval"


def test_safety_failure_forces_rejection() -> None:
    review, _ = prepare_chain(tx(destination="attacker-wallet"), NOW, ("e1",))
    assert review.verdicts[-1].verdict == "recommend_rejection"


def test_missing_evidence_is_challenged() -> None:
    review, _ = prepare_chain(tx(), NOW, ())
    assert review.verdicts[1].verdict == "challenge"


def test_verdict_must_match_role() -> None:
    with pytest.raises(ValidationError):
        AgentVerdict(
            agent_role="safety", proposal_sha256="0" * 64, verdict="propose",
            rationale="x", created_at=NOW,
        )


def test_arbiter_override_and_unbound_verdict_detected() -> None:
    review, _ = prepare_chain(tx(destination="attacker-wallet"), NOW, ("e1",))
    v = list(review.verdicts)
    v[-1] = v[-1].model_copy(update={"verdict": "recommend_approval"})
    assert verdicts_consistent(tuple(v), review.proposal) == "ARBITER_OVERRODE_BLOCK"
    other, _ = prepare_chain(tx(destination="other"), NOW, ("e1",))
    assert verdicts_consistent(other.verdicts, review.proposal) == "VERDICT_NOT_BOUND_TO_PROPOSAL"


def test_tampered_verdicts_fail_verification(tmp_path: Path) -> None:
    bundle, pub, _ = signed_bundle(tmp_path)
    data = json.loads((bundle / MANIFEST_FILE).read_text())
    data["review"]["verdicts"][2]["verdict"] = "fail"
    (bundle / MANIFEST_FILE).write_text(json.dumps(data))
    assert not verify_bundle(bundle, pub).passed


def test_ui_requires_exact_hash_and_name(tmp_path: Path) -> None:
    priv, _ = signing.generate_keypair(tmp_path / "k")
    key = signing.load_private(priv)
    review, h = prepare_chain(tx(), NOW, ("e1",))
    out = tmp_path / "o"
    bad = {"sha": "0" * 64, "name": "Human Reviewer"}
    ok, _ = handle_approval(review, bad, out, key)
    assert not ok and not (tmp_path / "o").exists()
    ok, _ = handle_approval(review, {"sha": h, "name": ""}, out, key)
    assert not ok and not out.exists()
    good = {"sha": h.upper(), "name": "Human Reviewer"}
    ok, _ = handle_approval(review, good, out, key, NOW)
    assert ok and (tmp_path / "o" / DIGEST_FILE).is_file()


def test_ui_escapes_html() -> None:
    review, h = prepare_chain(tx(purpose="<script>alert(1)</script>"), NOW, ("e1",))
    page = render_page(review, h)
    assert "<script>alert" not in page and sha256(review) in page
