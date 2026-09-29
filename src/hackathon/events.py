"""Accountability events: one validated, hash-chained record per significant agent action.

Follows the Team Dusk spec. The event hash covers the canonical payload without
the hash itself or any anchor receipt (``bitcoin_txid``, ``block_height``,
``block_hash``), so receipts can be attached after the fact.

Only hashes of inputs/outputs go in events; payloads and secrets stay out.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from .canonical import sha256
from .schemas import Sha256, StrictModel

RECEIPT_FIELDS = frozenset({"event_hash", "bitcoin_txid", "block_height", "block_hash"})


class EventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    TASK_DELEGATED = "TASK_DELEGATED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    RESULT_PRODUCED = "RESULT_PRODUCED"
    RESULT_VERIFIED = "RESULT_VERIFIED"
    RUN_FINALIZED = "RUN_FINALIZED"


class AccountabilityEvent(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: Annotated[str, Field(min_length=1, max_length=100)]
    event_id: Annotated[str, Field(min_length=1, max_length=100)]
    agent_id: Annotated[str, Field(min_length=1, max_length=100)]
    role: Annotated[str, Field(min_length=1, max_length=50)]
    seq: Annotated[int, Field(ge=0)]
    event_type: EventType
    parent_event_id: str | None = None
    prev_event_hash: Sha256 | None = None
    timestamp_utc: datetime
    action_summary: Annotated[str, Field(min_length=1, max_length=300)]
    input_hash: Sha256 | None = None
    output_hash: Sha256 | None = None
    model: str | None = None
    policy_id: str | None = None
    event_hash: Sha256
    bitcoin_txid: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    block_height: Annotated[int, Field(ge=0)] | None = None
    block_hash: Sha256 | None = None

    @field_validator("timestamp_utc")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must include a timezone")
        return value


def digest_payload(value: str | dict[str, Any] | list[Any] | None) -> str | None:
    """SHA-256 commitment to an input/output. The payload itself never leaves the caller."""
    if value is None:
        return None
    if isinstance(value, str):
        return hashlib.sha256(value.encode()).hexdigest()
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def compute_event_hash(event: AccountabilityEvent) -> str:
    payload = event.model_dump(mode="json", by_alias=True, exclude=set(RECEIPT_FIELDS))
    return sha256(payload)


def trace_commitment(event_hashes: list[str]) -> str:
    """Commitment to the whole ordered trace; RUN_FINALIZED carries it."""
    return sha256({"event_hashes": event_hashes})


def save_log(path: Path, events: list[AccountabilityEvent]) -> None:
    lines = [
        json.dumps(e.model_dump(mode="json", by_alias=True), sort_keys=True) for e in events
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_log(path: Path) -> list[AccountabilityEvent]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(AccountabilityEvent.model_validate(json.loads(line), strict=False))
    return events


class Recorder:
    """Creates, hashes, chains and (optionally) anchors events for one run."""

    def __init__(self, run_id: str, ledger=None, clock=None) -> None:
        self.run_id = run_id
        self.ledger = ledger
        self._clock = clock or (lambda: datetime.now(UTC))
        self.events: list[AccountabilityEvent] = []
        self._last_hash: dict[str, str] = {}
        self._seq: dict[str, int] = {}

    def emit(
        self,
        agent_id: str,
        role: str,
        event_type: EventType,
        summary: str,
        input_payload: str | dict[str, Any] | list[Any] | None = None,
        output_payload: str | dict[str, Any] | list[Any] | None = None,
        parent_event_id: str | None = None,
        model: str | None = None,
        policy_id: str | None = None,
        output_hash: str | None = None,
        input_hash: str | None = None,
    ) -> AccountabilityEvent:
        seq = self._seq.get(agent_id, 0)
        draft = AccountabilityEvent(
            run_id=self.run_id,
            event_id=str(uuid.uuid4()),
            agent_id=agent_id,
            role=role,
            seq=seq,
            event_type=event_type,
            parent_event_id=parent_event_id,
            prev_event_hash=self._last_hash.get(agent_id),
            timestamp_utc=self._clock(),
            action_summary=summary,
            input_hash=input_hash or digest_payload(input_payload),
            output_hash=output_hash or digest_payload(output_payload),
            model=model,
            policy_id=policy_id,
            event_hash="0" * 64,
        )
        event = draft.model_copy(update={"event_hash": compute_event_hash(draft)})
        if self.ledger is not None:
            txid = self.ledger.anchor_event(event.event_hash, agent_id, seq)
            event = event.model_copy(update={"bitcoin_txid": txid})
        self.events.append(event)
        self._last_hash[agent_id] = event.event_hash
        self._seq[agent_id] = seq + 1
        return event

    def confirm(self) -> None:
        """Attach block height/hash receipts once anchors are mined."""
        if self.ledger is None:
            return
        updated = []
        for event in self.events:
            if event.bitcoin_txid is None:
                updated.append(event)
                continue
            receipt = self.ledger.get_anchor(event.bitcoin_txid)
            updated.append(
                event.model_copy(
                    update={"block_height": receipt.block_height, "block_hash": receipt.block_hash}
                )
            )
        self.events = updated
