"""Evidence bundles on disk and the independent, deterministic verifier.

A bundle is a directory holding:

* ``manifest.json``: the serialized EvidenceManifest.
* ``manifest.sha256``: the digest recorded when the manifest was finalized.
* ``manifest.dsse.json``: optional DSSE-style Ed25519 signature envelope.

The verifier trusts none of these files. It re-validates the schema, recomputes
every hash, checks agent-verdict binding, and, when given a trusted public key,
verifies the signature. Each check reports PASS/FAIL with a stable reason code.

Without a trusted public key, someone who alters the manifest *and* rewrites
``manifest.sha256`` will not be caught; the signature check is what closes that.
The bundle shows origin, integrity and chronology of the record. It does not show
that the agents' proposals were correct.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import ValidationError

from . import signing
from .canonical import canonical_bytes, sha256
from .roles import verdicts_consistent
from .schemas import EvidenceManifest, ReviewPackage

MANIFEST_FILE = "manifest.json"
DIGEST_FILE = "manifest.sha256"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str = ""
    info: bool = False


@dataclass
class VerificationResult:
    bundle: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        graded = [check for check in self.checks if not check.info]
        return bool(graded) and all(check.passed for check in graded)

    def add(self, name: str, passed: bool, reason: str = "") -> None:
        self.checks.append(Check(name, passed, reason))

    def note(self, name: str) -> None:
        self.checks.append(Check(name, True, info=True))

    def render(self) -> str:
        lines = [f"VERIFY {self.bundle}"]
        for check in self.checks:
            tag = "INFO" if check.info else ("PASS" if check.passed else "FAIL")
            suffix = f" ({check.reason})" if check.reason and not check.passed else ""
            lines.append(f"  [{tag}] {check.name}{suffix}")
        lines.append(f"RESULT: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def write_bundle(
    directory: Path,
    manifest: EvidenceManifest,
    manifest_sha256: str,
    private_key: Ed25519PrivateKey | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json", by_alias=True)
    (directory / MANIFEST_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (directory / DIGEST_FILE).write_text(manifest_sha256 + "\n", encoding="utf-8")
    if private_key is not None:
        envelope = signing.sign(canonical_bytes(manifest), private_key)
        (directory / signing.ENVELOPE_FILE).write_text(
            json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def verify_bundle(
    directory: Path, public_key: Ed25519PublicKey | None = None
) -> VerificationResult:
    result = VerificationResult(bundle=str(directory))
    manifest_path = directory / MANIFEST_FILE
    digest_path = directory / DIGEST_FILE

    if not manifest_path.is_file():
        result.add("manifest present", False, "MISSING_MANIFEST")
        return result
    result.add("manifest present", True)

    if not digest_path.is_file():
        result.add("recorded digest present", False, "MISSING_DIGEST")
        return result
    recorded = digest_path.read_text(encoding="utf-8").strip()
    if not _HEX64.match(recorded):
        result.add("recorded digest present", False, "MALFORMED_DIGEST")
        return result
    result.add("recorded digest present", True)

    try:
        manifest = EvidenceManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8")), strict=False
        )
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        result.add("schema valid", False, f"SCHEMA_INVALID: {type(error).__name__}")
        return result
    result.add("schema valid", True)

    computed_review = sha256(manifest.review)
    result.add(
        "review hash recomputed",
        computed_review == manifest.review_sha256,
        "REVIEW_HASH_MISMATCH",
    )
    approvals = manifest.all_approvals
    result.add(
        "human approvals bound to review hash",
        all(a.review_sha256 == computed_review for a in approvals),
        "APPROVAL_NOT_BOUND",
    )
    distinct = len({a.approver.strip().lower() for a in approvals})
    result.add(
        f"{manifest.required_approvals} distinct approver(s) required",
        distinct >= manifest.required_approvals,
        "NOT_ENOUGH_APPROVERS",
    )
    if manifest.review.proposal is not None:
        problem = verdicts_consistent(manifest.review.verdicts, manifest.review.proposal)
        result.add("agent verdicts bound to proposal hash", not problem, problem)
    else:
        result.note("agent verdicts: none recorded (legacy review)")
    result.add(
        "manifest digest matches recorded digest",
        sha256(manifest) == recorded,
        "MANIFEST_DIGEST_MISMATCH",
    )

    if public_key is None:
        result.note("signature not checked (no trusted public key supplied)")
        return result
    envelope_path = directory / signing.ENVELOPE_FILE
    if not envelope_path.is_file():
        result.add("signature valid", False, "MISSING_SIGNATURE")
        return result
    ok, reason, payload = signing.verify_envelope(
        envelope_path.read_text(encoding="utf-8"), public_key
    )
    result.add("signature valid", ok, reason)
    if ok:
        result.add(
            "signed payload is this manifest",
            payload == canonical_bytes(manifest),
            "SIGNED_PAYLOAD_MISMATCH",
        )
    return result


def build_demo(root: Path) -> dict[str, Path]:
    """Create signed demo bundles under ``root`` (must be named ``demo``).

    * golden: clean and signed.
    * tampered: one field edited; digest and signature both fail.
    * tampered-rehashed: field edited AND digest file rewritten; only the
      signature check catches it.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from .roles import DEMO_HOA_POLICY
    from .schemas import Transaction
    from .workflow import approve, finalize, prepare_chain

    reset_demo(root)
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    transaction = Transaction(
        transaction_id="hoa-2291",
        asset="USD",
        amount=Decimal("4800.00"),
        destination="Blue Lagoon Pool Services",
        purpose="Pool repair, invoice 2291 (budget line: Pool Maintenance)",
    )
    review, review_hash = prepare_chain(
        transaction,
        now,
        evidence_refs=("invoice:2291", "budget:pool-maintenance"),
        policy=DEMO_HOA_POLICY,
    )
    first = approve(review, review_hash, "Board Member A (demo)", now)
    second = approve(review, review_hash, "Board Member B (demo)", now)
    manifest, manifest_hash = finalize(review, first, (second,), required_approvals=2)

    private_path, _ = signing.generate_keypair(root / "keys", "demo-signer")
    golden = root / "golden"
    write_bundle(golden, manifest, manifest_hash, signing.load_private(private_path))

    def edited_copy(name: str, rehash: bool) -> Path:
        target = root / name
        shutil.copytree(golden, target)
        path = target / MANIFEST_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        data["review"]["transaction"]["destination"] = "Unknown LLC"
        if rehash:
            # A capable forger recomputes every hash inside and outside the manifest.
            forged_review = ReviewPackage.model_validate(data["review"], strict=False)
            data["review_sha256"] = sha256(forged_review)
            data["approval"]["review_sha256"] = data["review_sha256"]
            for extra in data["additional_approvals"]:
                extra["review_sha256"] = data["review_sha256"]
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if rehash:
            forged = EvidenceManifest.model_validate(data, strict=False)
            (target / DIGEST_FILE).write_text(sha256(forged) + "\n", encoding="utf-8")
        return target

    return {
        "golden": golden,
        "tampered": edited_copy("tampered", rehash=False),
        "tampered-rehashed": edited_copy("tampered-rehashed", rehash=True),
        "public_key": root / "keys" / "demo-signer.pub",
    }


def reset_demo(root: Path) -> None:
    """Delete a demo directory. Refuses anything not named ``demo`` as a safety guard."""
    if root.name != "demo":
        raise ValueError("refusing to reset a directory not named 'demo'")
    if root.exists():
        shutil.rmtree(root)
