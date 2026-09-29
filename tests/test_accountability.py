from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from hackathon.auditor import AuditFailedError, audit_run, finalize_run
from hackathon.chain_demo import (
    LEDGER_FILE,
    LOG_FILE,
    audit_directory,
    build_chain_demo,
    run_demo_trace,
)
from hackathon.events import (
    AccountabilityEvent,
    EventType,
    Recorder,
    compute_event_hash,
    digest_payload,
    load_log,
)
from hackathon.ledger import (
    PREFIX,
    BitcoinCoreLedger,
    RpcError,
    SimulatedLedger,
    anchor_payload_hex,
)


class FakeRpc:
    """Just enough of Bitcoin Core's RPC surface to exercise the adapter."""

    def __init__(self) -> None:
        self.txs: dict[str, dict] = {}
        self.blocks: list[str] = []
        self.calls: list[str] = []
        self.wallet_exists = False
        self.chain = "regtest"

    def call(self, method, params=None, wallet=None):
        params = params or []
        self.calls.append(method)
        if method == "createwallet":
            if self.wallet_exists:
                raise RpcError("exists")
            self.wallet_exists = True
            return {"name": params[0]}
        if method == "getblockchaininfo":
            return {"chain": self.chain}
        if method == "getnewaddress":
            return "bcrt1qfake"
        if method == "getbalance":
            return 50 if self.blocks else 0
        if method == "generatetoaddress":
            new = [f"{len(self.blocks) + i:064x}" for i in range(params[0])]
            self.blocks += new
            for tx in self.txs.values():
                tx.setdefault("blockhash", new[-1])
            return new
        if method == "createrawtransaction":
            return "raw:" + params[1][0]["data"]
        if method == "fundrawtransaction":
            return {"hex": "funded:" + params[0].removeprefix("raw:")}
        if method == "signrawtransactionwithwallet":
            return {"hex": "signed:" + params[0].removeprefix("funded:"), "complete": True}
        if method == "sendrawtransaction":
            payload = params[0].removeprefix("signed:")
            txid = f"{len(self.txs) + 1:064x}"
            self.txs[txid] = {
                "vout": [
                    {"scriptPubKey": {"type": "nulldata", "hex": "6a25" + payload}},
                    {"scriptPubKey": {"type": "witness_v0_keyhash", "hex": "0014aa"}},
                ]
            }
            return txid
        if method == "getrawtransaction":
            if params[0] not in self.txs:
                raise RpcError("No such transaction")
            return self.txs[params[0]]
        if method == "getblockheader":
            return {"height": self.blocks.index(params[0]) + 1}
        raise RpcError(f"unsupported {method}")


def test_payload_is_prefix_version_hash_only() -> None:
    payload = bytes.fromhex(anchor_payload_hex("ab" * 32))
    assert payload.startswith(PREFIX) and len(payload) == 4 + 1 + 32


def test_bitcoin_core_adapter_round_trip_against_fake_rpc() -> None:
    ledger = BitcoinCoreLedger(FakeRpc())
    txid = ledger.anchor_event("cd" * 32, "agent", 0)
    assert ledger.verify_anchor("cd" * 32, txid)
    assert not ledger.verify_anchor("ef" * 32, txid)
    assert not ledger.verify_anchor("cd" * 32, "0" * 64)
    assert not ledger.get_anchor(txid).confirmed or ledger.get_anchor(txid).block_height
    ledger.mine_demo_block()
    receipt = ledger.get_anchor(txid)
    assert receipt.confirmed and receipt.block_height is not None


def test_events_hash_is_deterministic_and_excludes_receipts() -> None:
    a = Recorder("r", clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    e = a.emit("o", "orchestrator", EventType.RUN_STARTED, "start")
    with_receipt = e.model_copy(update={"bitcoin_txid": "1" * 64, "block_height": 5})
    assert compute_event_hash(with_receipt) == e.event_hash


def test_event_schema_rejects_extra_fields_and_naive_time() -> None:
    e = Recorder("r").emit("o", "orchestrator", EventType.RUN_STARTED, "start")
    payload = e.model_dump(mode="json")
    with pytest.raises(ValidationError):
        AccountabilityEvent.model_validate({**payload, "extra": 1}, strict=False)
    with pytest.raises(ValidationError):
        AccountabilityEvent.model_validate(
            {**payload, "timestamp_utc": "2026-01-01T00:00:00"}, strict=False
        )


def test_digest_payload_is_order_independent_and_hides_payload() -> None:
    assert digest_payload({"a": 1, "b": 2}) == digest_payload({"b": 2, "a": 1})
    assert digest_payload(None) is None


def test_golden_trace_passes_and_is_finalized() -> None:
    ledger = SimulatedLedger()
    recorder = run_demo_trace(ledger)
    assert recorder.events[-1].event_type == EventType.RUN_FINALIZED
    assert [e.seq for e in recorder.events if e.agent_id == "orchestrator"] == list(range(5))
    assert audit_run(recorder.events, ledger).passed
    # A ledger that never saw these anchors must not verify them.
    assert not audit_run(recorder.events, SimulatedLedger()).passed


def test_finalization_is_gated_on_audit() -> None:
    ledger = SimulatedLedger()
    recorder = Recorder("gate", ledger)
    recorder.emit("orchestrator", "orchestrator", EventType.RUN_STARTED, "start")
    with pytest.raises(AuditFailedError):  # missing delegated/action/result events
        finalize_run(recorder, ledger)
    assert all(e.event_type != EventType.RUN_FINALIZED for e in recorder.events)


def test_tampering_variants_all_fail_and_golden_passes(tmp_path: Path) -> None:
    root = tmp_path / "chain"
    dirs = build_chain_demo(root)
    assert audit_directory(dirs["golden"]).passed
    for name in ("tampered", "tampered-rehashed", "deleted-event"):
        assert not audit_directory(dirs[name]).passed, name
    assert "ANCHOR_MISMATCH" in audit_directory(dirs["tampered-rehashed"]).render()


def test_missing_anchor_fails(tmp_path: Path) -> None:
    dirs = build_chain_demo(tmp_path / "chain")
    events = load_log(dirs["golden"] / LOG_FILE)
    events[2] = events[2].model_copy(update={"bitcoin_txid": None})
    ledger = SimulatedLedger(dirs["golden"] / LEDGER_FILE)
    assert "MISSING_ANCHOR" in audit_run(events, ledger).render()


def test_reordered_events_fail(tmp_path: Path) -> None:
    dirs = build_chain_demo(tmp_path / "chain")
    events = load_log(dirs["golden"] / LOG_FILE)
    events[2], events[3] = events[3], events[2]
    ledger = SimulatedLedger(dirs["golden"] / LEDGER_FILE)
    assert not audit_run(events, ledger).passed


def test_chain_demo_refuses_wrong_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_chain_demo(tmp_path / "important")


def test_no_payload_text_in_events(tmp_path: Path) -> None:
    dirs = build_chain_demo(tmp_path / "chain")
    log = (dirs["golden"] / LOG_FILE).read_text()
    assert "combined result" not in log and "25 USDC" not in log


def test_adapter_refuses_non_regtest_node() -> None:
    rpc = FakeRpc()
    rpc.chain = "main"
    with pytest.raises(RpcError, match="not regtest"):
        BitcoinCoreLedger(rpc).anchor_event("cd" * 32, "agent", 0)
    assert "createrawtransaction" not in rpc.calls


def test_full_demo_runs_against_fake_bitcoin_ledger(tmp_path: Path) -> None:
    ledger = BitcoinCoreLedger(FakeRpc())
    dirs = build_chain_demo(tmp_path / "chain", ledger)
    assert audit_directory(dirs["golden"], ledger).passed
    assert not audit_directory(dirs["tampered-rehashed"], ledger).passed
