"""Small operational CLI for configuration and the core smoke path."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

from .config import status
from .schemas import Transaction
from .workflow import approve, finalize, prepare, verify


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Collaborative-agent core utilities")
    parser.add_argument("command", choices=("config", "smoke"))
    args = parser.parse_args()
    return _config() if args.command == "config" else _smoke()
