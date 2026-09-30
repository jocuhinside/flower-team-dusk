"""Small operational CLI for configuration and the core smoke path."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .bundle import build_demo, reset_demo, verify_bundle
from .chain_demo import audit_directory, build_chain_demo, proof_summary
from .config import status
from .ledger import RpcError, ledger_from_environment
from .roles import DEMO_HOA_POLICY
from .schemas import Transaction
from .signing import generate_keypair, load_private, load_public
from .ui import serve
from .workflow import approve, finalize, prepare, prepare_chain, verify


def _smoke() -> int:
    transaction = Transaction(
        transaction_id="smoke-001",
        asset="USDC",
        amount=Decimal("10.00"),
        destination="demo-merchant",
        purpose="Hackathon end-to-end smoke test",
    )
    review, review_hash = prepare(transaction)
    approval = approve(
        review,
        confirmed_sha256=review_hash,
        approver="Smoke Test Human",
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest, manifest_hash = finalize(review, approval)
    verify(manifest, manifest_hash)
    print(
        json.dumps(
            {
                "status": "passed",
                "review_sha256": review_hash,
                "manifest_sha256": manifest_hash,
                "human_gate": "confirmed exact review hash (simulated smoke approver)",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _config() -> int:
    configured = status()
    print(json.dumps({"configured": configured, "secrets_printed": False}, indent=2))
    return 0 if all(configured.values()) else 2


DEMO_ROOT = Path("demo")
KEYS_DIR = Path("keys")


def _demo() -> int:
    bundles = build_demo(DEMO_ROOT)
    pub = bundles["public_key"]
    print("Built signed demo bundles under demo/.")
    for name in ("golden", "tampered", "tampered-rehashed"):
        expected = "PASS" if name == "golden" else "FAIL"
        print(f"  hackathon verify {bundles[name]} --pubkey {pub}   ->  {expected}")
    return 0


def _reset() -> int:
    reset_demo(DEMO_ROOT)
    print("Demo state cleared. Run `hackathon demo` to rebuild.")
    return 0


def _verify(bundle: str | None, pubkey: str | None) -> int:
    if bundle is None:
        print("usage: hackathon verify <bundle-directory> [--pubkey <file>]")
        return 2
    key = load_public(Path(pubkey)) if pubkey else None
    result = verify_bundle(Path(bundle), key)
    print(result.render())
    return 0 if result.passed else 1


def _ledger(kind: str):
    return ledger_from_environment() if kind == "bitcoin" else None


def _chain_demo(kind: str) -> int:
    root = DEMO_ROOT / "chain"
    dirs = build_chain_demo(root, _ledger(kind))
    label = "Bitcoin Core regtest" if kind == "bitcoin" else "SIMULATED ledger, not Bitcoin"
    print(f"Built accountability-chain demo ({label}) under demo/chain/.")
    print(proof_summary(dirs["golden"]))
    for name, expected in (
        ("golden", "PASS"),
        ("tampered", "FAIL"),
        ("tampered-rehashed", "FAIL"),
        ("deleted-event", "FAIL"),
    ):
        flag = " --ledger bitcoin" if kind == "bitcoin" else ""
        print(f"  hackathon audit-chain {dirs[name]}{flag}   ->  {expected}")
    return 0


def _audit_chain(directory: str | None, kind: str) -> int:
    if directory is None:
        print("usage: hackathon audit-chain <directory with events.jsonl and ledger.json>")
        return 2
    result = audit_directory(Path(directory), _ledger(kind))
    print(result.render())
    return 0 if result.passed else 1


def _keygen() -> int:
    private, public = generate_keypair(KEYS_DIR)
    print(f"Wrote {private} (keep secret, never commit) and {public}.")
    return 0


def _approve_ui(out: str, approvers: int) -> int:
    private_path = KEYS_DIR / "signer.key"
    if not private_path.is_file():
        print("No signer key. Run `hackathon keygen` first.")
        return 2
    now = datetime.now(UTC)
    transaction = Transaction(
        transaction_id=f"ui-{now:%Y%m%d%H%M%S}",
        asset="USD",
        amount=Decimal("4800.00"),
        destination="Blue Lagoon Pool Services",
        purpose="Pool repair, invoice 2291 (budget line: Pool Maintenance)",
    )
    review, _ = prepare_chain(
        transaction, now, evidence_refs=("invoice:2291",), policy=DEMO_HOA_POLICY
    )
    serve(review, Path(out), load_private(private_path), required=approvers)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Collaborative-agent core utilities")
    parser.add_argument(
        "command", choices=(
            "config", "smoke", "demo", "verify", "reset", "keygen", "approve-ui",
            "chain-demo", "audit-chain",
        )
    )
    parser.add_argument("bundle", nargs="?")
    parser.add_argument("--pubkey")
    parser.add_argument("--out", default="demo/live")
    parser.add_argument("--approvers", type=int, default=2)
    parser.add_argument("--ledger", choices=("simulated", "bitcoin"), default="simulated")
    args = parser.parse_args()
    if args.command == "config":
        return _config()
    if args.command == "smoke":
        return _smoke()
    if args.command == "demo":
        return _demo()
    if args.command == "reset":
        return _reset()
    if args.command in ("chain-demo", "audit-chain"):
        try:
            if args.command == "chain-demo":
                return _chain_demo(args.ledger)
            return _audit_chain(args.bundle, args.ledger)
        except RpcError as error:
            print(f"Bitcoin ledger error: {error}")
            return 2
    if args.command == "keygen":
        return _keygen()
    if args.command == "approve-ui":
        return _approve_ui(args.out, args.approvers)
    return _verify(args.bundle, args.pubkey)
