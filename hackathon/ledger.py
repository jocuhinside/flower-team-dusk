"""Accountability ledger adapters.

``SimulatedLedger`` is an in-process stand-in used for tests and the offline demo.
It is NOT a blockchain: it has no consensus and nothing about it is independent.
``BitcoinCoreLedger`` talks to a Bitcoin Core node over JSON-RPC and anchors a
short commitment in an OP_RETURN output. Intended for regtest only; it has been
exercised in unit tests against a fake RPC, not yet against a live node.

Only ``prefix + version + event_hash`` is ever written to a transaction.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PREFIX = b"SBAC"
VERSION = b"\x01"


def anchor_payload_hex(event_hash: str) -> str:
    if len(event_hash) != 64:
        raise ValueError("event_hash must be 64 hex characters")
    return (PREFIX + VERSION + bytes.fromhex(event_hash)).hex()


@dataclass(frozen=True)
class AnchorReceipt:
    txid: str
    confirmed: bool
    block_height: int | None = None
    block_hash: str | None = None


class AccountabilityLedger(Protocol):
    def anchor_event(self, event_hash: str, agent_id: str, seq: int) -> str: ...

    def get_anchor(self, txid: str) -> AnchorReceipt: ...

    def verify_anchor(self, event_hash: str, txid: str) -> bool: ...

    def mine_demo_block(self) -> str: ...


class SimulatedLedger:
    """Deterministic, file-backed fake ledger. Labeled SIMULATED everywhere it is shown."""

    name = "SIMULATED (not Bitcoin)"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.txs: dict[str, dict[str, Any]] = {}
        self.blocks: list[str] = []
        if path is not None and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.txs, self.blocks = data["txs"], data["blocks"]

    def _save(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"txs": self.txs, "blocks": self.blocks}, sort_keys=True, indent=1),
                encoding="utf-8",
            )

    def anchor_event(self, event_hash: str, agent_id: str, seq: int) -> str:
        payload = anchor_payload_hex(event_hash)
        txid = hashlib.sha256(f"{len(self.txs)}:{payload}".encode()).hexdigest()
        self.txs[txid] = {"payload": payload, "block": None}
        self._save()
        return txid

    def mine_demo_block(self) -> str:
        block_hash = hashlib.sha256(f"block:{len(self.blocks)}".encode()).hexdigest()
        self.blocks.append(block_hash)
        for tx in self.txs.values():
            if tx["block"] is None:
                tx["block"] = block_hash
        self._save()
        return block_hash

    def get_anchor(self, txid: str) -> AnchorReceipt:
        tx = self.txs.get(txid)
        if tx is None:
            raise KeyError(txid)
        block = tx["block"]
        height = self.blocks.index(block) + 1 if block else None
        return AnchorReceipt(txid, block is not None, height, block)

    def verify_anchor(self, event_hash: str, txid: str) -> bool:
        tx = self.txs.get(txid)
        return tx is not None and tx["payload"] == anchor_payload_hex(event_hash)


class RpcError(RuntimeError):
    pass


class BitcoinRpc:
    """Minimal JSON-RPC client (stdlib only). Use a regtest node you control."""

    def __init__(self, url: str, user: str, password: str) -> None:
        self.url, self._auth = url.rstrip("/"), base64.b64encode(f"{user}:{password}".encode())

    def call(self, method: str, params: list[Any] | None = None, wallet: str | None = None) -> Any:
        endpoint = f"{self.url}/wallet/{wallet}" if wallet else self.url
        body = json.dumps({"jsonrpc": "1.0", "id": "sb", "method": method, "params": params or []})
        request = urllib.request.Request(
            endpoint,
            data=body.encode(),
            headers={"Authorization": f"Basic {self._auth.decode()}", "Content-Type": "text/plain"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                reply = json.loads(response.read())
        except urllib.error.HTTPError as error:
            reply = json.loads(error.read() or b"{}")
        if reply.get("error"):
            raise RpcError(f"{method}: {reply['error']}")
        return reply["result"]


class BitcoinCoreLedger:
    """Anchors event hashes in OP_RETURN outputs on a Bitcoin Core regtest node."""

    name = "Bitcoin Core regtest"

    def __init__(self, rpc: Any, wallet: str = "anchor") -> None:
        self.rpc, self.wallet = rpc, wallet
        self._address: str | None = None

    def _wallet_call(self, method: str, params: list[Any] | None = None) -> Any:
        return self.rpc.call(method, params, wallet=self.wallet)

    def _ensure_ready(self) -> str:
        if self._address is not None:
            return self._address
        chain = self.rpc.call("getblockchaininfo").get("chain")
        if chain != "regtest":
            raise RpcError(f"refusing to anchor: node is on {chain!r}, not regtest")
        try:
            self.rpc.call("createwallet", [self.wallet])
        except RpcError:
            with contextlib.suppress(RpcError):  # already loaded is fine
                self.rpc.call("loadwallet", [self.wallet])
        self._address = self._wallet_call("getnewaddress")
        if self._wallet_call("getbalance") < 1:
            self.rpc.call("generatetoaddress", [101, self._address])  # mature coinbase
        return self._address

    def anchor_event(self, event_hash: str, agent_id: str, seq: int) -> str:
        self._ensure_ready()
        outputs = [{"data": anchor_payload_hex(event_hash)}]
        raw = self.rpc.call("createrawtransaction", [[], outputs])
        funded = self._wallet_call("fundrawtransaction", [raw])["hex"]
        signed = self._wallet_call("signrawtransactionwithwallet", [funded])
        if not signed.get("complete"):
            raise RpcError("transaction signing incomplete")
        return self.rpc.call("sendrawtransaction", [signed["hex"]])

    def mine_demo_block(self) -> str:
        return self.rpc.call("generatetoaddress", [1, self._ensure_ready()])[0]

    def get_anchor(self, txid: str) -> AnchorReceipt:
        tx = self.rpc.call("getrawtransaction", [txid, True])
        block_hash = tx.get("blockhash")
        if not block_hash:
            return AnchorReceipt(txid, False)
        height = self.rpc.call("getblockheader", [block_hash])["height"]
        return AnchorReceipt(txid, True, height, block_hash)

    def verify_anchor(self, event_hash: str, txid: str) -> bool:
        try:
            tx = self.rpc.call("getrawtransaction", [txid, True])
        except RpcError:
            return False
        expected = anchor_payload_hex(event_hash)
        for output in tx.get("vout", []):
            script = output.get("scriptPubKey", {})
            if script.get("type") == "nulldata" and script.get("hex", "").endswith(expected):
                return True
        return False


def ledger_from_environment() -> BitcoinCoreLedger:
    """Build a regtest ledger from BITCOIN_RPC_URL / _USER / _PASSWORD (no defaults for secrets)."""
    import os

    user, password = os.environ.get("BITCOIN_RPC_USER"), os.environ.get("BITCOIN_RPC_PASSWORD")
    if not user or not password:
        raise RpcError("set BITCOIN_RPC_USER and BITCOIN_RPC_PASSWORD (see scripts/regtest_up.sh)")
    url = os.environ.get("BITCOIN_RPC_URL", "http://127.0.0.1:18443")
    return BitcoinCoreLedger(BitcoinRpc(url, user, password))
