"""Auditor: recompute hashes, check chains, check anchors, gate finalization.

This is an internal check inside the system, not a third-party verifier. It shows
the recorded trace is internally consistent and matches its anchors; it does not
show that any agent's output was correct.
"""

from __future__ import annotations

from .bundle import VerificationResult
from .events import (
    AccountabilityEvent,
    EventType,
    Recorder,
    compute_event_hash,
    trace_commitment,
)

REQUIRED_BEFORE_FINALIZE = (
    EventType.RUN_STARTED,
    EventType.TASK_DELEGATED,
    EventType.ACTION_COMPLETED,
    EventType.RESULT_PRODUCED,
)


class AuditFailedError(RuntimeError):
    def __init__(self, result: VerificationResult) -> None:
        super().__init__(result.render())
        self.result = result


def audit_run(events: list[AccountabilityEvent], ledger=None) -> VerificationResult:
    run_id = events[0].run_id if events else "(empty)"
    result = VerificationResult(bundle=f"run {run_id}")
    if not events:
        result.add("events present", False, "EMPTY_TRACE")
        return result
    result.add("events present", True)
    result.add("single run_id", all(e.run_id == run_id for e in events), "MIXED_RUN_IDS")

    bad_hash = [e.event_id for e in events if compute_event_hash(e) != e.event_hash]
    result.add("event hashes recomputed", not bad_hash, f"EVENT_HASH_MISMATCH x{len(bad_hash)}")

    problems = []
    last: dict[str, AccountabilityEvent] = {}
    for event in events:
        prior = last.get(event.agent_id)
        if prior is None and (event.seq != 0 or event.prev_event_hash is not None):
            problems.append(f"{event.agent_id}: first event must be seq 0 with no prev hash")
        elif prior is not None and (
            event.seq != prior.seq + 1 or event.prev_event_hash != prior.event_hash
        ):
            problems.append(f"{event.agent_id}: chain broken at seq {event.seq}")
        last[event.agent_id] = event
    result.add("per-agent chains continuous", not problems, "CHAIN_BROKEN: " + "; ".join(problems))

    types = {e.event_type for e in events}
    missing = [t.value for t in REQUIRED_BEFORE_FINALIZE if t not in types]
    result.add("required event types present", not missing, "MISSING_TYPES: " + ", ".join(missing))
    result.add(
        "run starts with RUN_STARTED",
        events[0].event_type == EventType.RUN_STARTED,
        "RUN_NOT_STARTED_FIRST",
    )

    finals = [i for i, e in enumerate(events) if e.event_type == EventType.RUN_FINALIZED]
    if finals:
        index = finals[0]
        expected = trace_commitment([e.event_hash for e in events[:index]])
        result.add(
            "finalization commits to full trace",
            events[index].output_hash == expected,
            "TRACE_COMMITMENT_MISMATCH",
        )
        result.add(
            "RESULT_VERIFIED precedes finalization",
            any(e.event_type == EventType.RESULT_VERIFIED for e in events[:index]),
            "FINALIZED_WITHOUT_VERIFICATION",
        )

    if ledger is None:
        result.note("anchors not checked (no ledger supplied)")
        return result
    unanchored = [e.event_id for e in events if e.bitcoin_txid is None]
    result.add("every event anchored", not unanchored, f"MISSING_ANCHOR x{len(unanchored)}")
    mismatched = [
        e.event_id
        for e in events
        if e.bitcoin_txid is not None and not ledger.verify_anchor(e.event_hash, e.bitcoin_txid)
    ]
    result.add("anchors match event hashes", not mismatched, f"ANCHOR_MISMATCH x{len(mismatched)}")
    confirmed = sum(1 for e in events if e.block_height is not None)
    result.note(f"anchor confirmations: {confirmed}/{len(events)} events have block receipts")
    return result


def finalize_run(recorder: Recorder, ledger, auditor_id: str = "auditor") -> AccountabilityEvent:
    """Gate: only a passing audit lets the run be marked RUN_FINALIZED."""
    result = audit_run(recorder.events, ledger)
    if not result.passed:
        raise AuditFailedError(result)
    verified = recorder.emit(
        auditor_id,
        "auditor",
        EventType.RESULT_VERIFIED,
        "Audit passed: hashes, chains and anchors verified",
        output_payload=result.render(),
    )
    return recorder.emit(
        "orchestrator",
        "orchestrator",
        EventType.RUN_FINALIZED,
        "Run finalized after successful audit",
        parent_event_id=verified.event_id,
        output_hash=trace_commitment([e.event_hash for e in recorder.events]),
    )
