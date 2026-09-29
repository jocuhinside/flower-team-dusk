"""Offline accountability-chain demo using simulated specialist agents.

Flower agents replace ``simulated_specialists``; the recorder, ledger and auditor
calls stay the same. Uses the SimulatedLedger unless a real ledger is passed.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .auditor import audit_run, finalize_run
from .events import (
    AccountabilityEvent,
    EventType,
    Recorder,
    compute_event_hash,
    load_log,
    save_log,
)
from .ledger import SimulatedLedger

LOG_FILE = "events.jsonl"
LEDGER_FILE = "ledger.json"


def _clock():
    start = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    counter = {"n": 0}

    def tick() -> datetime:
        counter["n"] += 1
        return start + timedelta(seconds=counter["n"])

    return tick


def run_demo_trace(ledger, run_id: str = "demo-run-001") -> Recorder:
    recorder = Recorder(run_id, ledger, clock=_clock())
    task = "Review a $4,800 pool repair invoice from Blue Lagoon Pool Services"
    started = recorder.emit(
        "orchestrator", "orchestrator", EventType.RUN_STARTED, "Run started", input_payload=task
    )
    for specialist, role, job in (
        ("analyst-agent", "analyst", "Extract payee, amount and purpose from the invoice"),
        ("safety-agent", "safety", "Check payee against the approved vendor list"),
    ):
        delegated = recorder.emit(
            "orchestrator",
            "orchestrator",
            EventType.TASK_DELEGATED,
            f"Delegated to {specialist}: {job}",
            input_payload={"to": specialist, "job": job},
            parent_event_id=started.event_id,
        )
        done = recorder.emit(
            specialist,
            role,
            EventType.ACTION_COMPLETED,
            job,
            input_payload=job,
            output_payload=f"{specialist} result",
            parent_event_id=delegated.event_id,
            model="simulated",
        )
        recorder.emit(
            specialist,
            role,
            EventType.RESULT_PRODUCED,
            f"{specialist} returned its result",
            output_payload=f"{specialist} result",
            parent_event_id=done.event_id,
            model="simulated",
        )
    recorder.emit(
        "orchestrator",
        "orchestrator",
        EventType.RESULT_PRODUCED,
        "Combined specialist results",
        output_payload="combined result",
    )
    ledger.mine_demo_block()
    recorder.confirm()
    finalize_run(recorder, ledger)
    ledger.mine_demo_block()
    recorder.confirm()
    return recorder


def _rehash_agent(events: list[AccountabilityEvent], index: int, **changes) -> None:
    """Forger: edit one event, then recompute its hash and its agent's later chain hashes."""
    agent = events[index].agent_id
    events[index] = events[index].model_copy(update=changes)
    previous = None
    for i, event in enumerate(events):
        if event.agent_id != agent or i < index:
            if event.agent_id == agent:
                previous = event.event_hash
            continue
        fixed = event.model_copy(update={"prev_event_hash": previous})
        fixed = fixed.model_copy(update={"event_hash": compute_event_hash(fixed)})
        events[i] = fixed
        previous = fixed.event_hash


def build_chain_demo(root: Path, ledger=None) -> dict[str, Path]:
    """Build golden + tampered runs. With no ``ledger`` a SimulatedLedger is used."""
    if root.name != "chain":
        raise ValueError("refusing to build outside a directory named 'chain'")
    if root.exists():
        shutil.rmtree(root)
    ledger_path = root / "golden" / LEDGER_FILE
    simulated = ledger is None
    if simulated:
        ledger = SimulatedLedger(ledger_path)
    recorder = run_demo_trace(ledger)
    save_log(root / "golden" / LOG_FILE, recorder.events)

    def variant(name: str, mutate) -> Path:
        target = root / name
        target.mkdir(parents=True)
        if simulated:
            shutil.copy(ledger_path, target / LEDGER_FILE)
        events = list(recorder.events)
        mutate(events)
        save_log(target / LOG_FILE, events)
        return target

    def edit_only(events: list[AccountabilityEvent]) -> None:
        events[3] = events[3].model_copy(update={"action_summary": "Edited after anchoring"})

    def edit_and_rehash(events: list[AccountabilityEvent]) -> None:
        _rehash_agent(events, 3, action_summary="Edited after anchoring")

    def delete_event(events: list[AccountabilityEvent]) -> None:
        del events[4]

    return {
        "golden": root / "golden",
        "tampered": variant("tampered", edit_only),
        "tampered-rehashed": variant("tampered-rehashed", edit_and_rehash),
        "deleted-event": variant("deleted-event", delete_event),
    }


def audit_directory(directory: Path, ledger=None):
    events = load_log(directory / LOG_FILE)
    if ledger is None:
        ledger = SimulatedLedger(directory / LEDGER_FILE)
    return audit_run(events, ledger)


def proof_summary(directory: Path) -> str:
    events = load_log(directory / LOG_FILE)
    lines = [f"run_id: {events[0].run_id}", f"events: {len(events)}"]
    for event in events:
        txid = (event.bitcoin_txid or "-")[:12]
        height = event.block_height if event.block_height is not None else "pending"
        lines.append(
            f"  {event.agent_id:<13} {event.event_type.value:<16} "
            f"{event.event_hash[:12]} -> tx {txid}  block {height}"
        )
    return "\n".join(lines)
