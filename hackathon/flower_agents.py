"""Accountable Flower agents: wrap Grid calls so every handoff becomes a recorded event.

Kept free of ``flwr`` imports so it can be tested with fakes. ``flower_app.py``
plugs in the real AgentSession.

Roles are detected from the tools the runtime exposes:

* SuperLink-side orchestrator: ``get_nodes``, ``push_messages``, ``pull_messages``.
* SuperNode-side specialist: ``push_reply_message`` only.

Specialists attach a small attestation (hashes only) to their reply. The
orchestrator verifies and records it on the specialist's behalf. Specialist
events are therefore NOT signed by the specialist; per-agent keys are a later step.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openai

from .auditor import AuditFailedError, audit_run, finalize_run
from .events import EventType, Recorder, digest_payload, save_log
from .ledger import SimulatedLedger

ENVELOPE_KEY = "sb_accountability"
ORCHESTRATOR = "orchestrator"

ORCHESTRATOR_INSTRUCTIONS = (
    "You are the orchestrator of a small team of specialist agents on Flower SuperGrid, "
    "reviewing a vendor payment for a homeowners association. Call get_nodes, then use "
    "push_messages to delegate different subtasks to at least two SuperNodes (for example: "
    "extract payee, amount and purpose from the invoice; check the payee against the approved "
    "vendor list and board-member links; check the budget line and reserve rules), then "
    "pull_messages to collect their replies. Combine the replies into a short report ending in "
    "a PAY or HOLD recommendation. Never execute or authorize any payment; two board members "
    "must approve the exact SHA-256 of the final review before anything is finalized."
)
# Budget for one SuperGrid task (5-minute limit): get_nodes, push, pull, final answer.
ORCHESTRATOR_MAX_TURNS = 4
MAX_OUTPUT_TOKENS = 1500
MODEL_CALL_TIMEOUT_S = 60

# Scripted orchestration: used when the orchestrator's own model endpoint is unreachable.
# Every model call then happens on the SuperNodes; the orchestrator only routes messages.
SCRIPTED_SUBTASKS = (
    ("invoice", "Extract the payee, amount, purpose and due date from this invoice request."),
    ("vendor", "Check whether the payee is stated to be on the approved vendor list and note "
               "any conflict-of-interest or missing-information concerns."),
    ("budget", "Check the amount against the stated budget line and reserve rules; say whether "
               "funds are sufficient."),
)
SCRIPTED_WAIT_S = 120
ARBITER_WAIT_S = 60
NO_DECISION = "HOLD (no PAY or HOLD decision from the arbiter)"

FINAL_TURN_NOTE = (
    " Tool use has ended. Answer now using only the results you already have, and say "
    "which results are missing."
)
SPECIALIST_INSTRUCTIONS = (
    "You are a specialist agent on a SuperNode. Do only the task in the instruction you were "
    "given, using only information you legitimately have. Reply exactly once with "
    "push_reply_message. Keep the reply concise and never include secrets."
)


# ---------- specialist reply attestation ----------


def wrap_reply(
    body: str,
    instruction: str,
    label: str,
    now: datetime | None = None,
    delivery: str = "model_tool_call",
) -> str:
    """Attach hash-only attestation to a specialist's reply.

    ``delivery`` records who sent it: the model via ``push_reply_message``, or the
    harness forwarding the model's final text because the model never called the tool.
    """
    return json.dumps(
        {
            ENVELOPE_KEY: "1",
            "body": body,
            "attestation": {
                "agent_label": label,
                "delivery": delivery,
                "input_hash": digest_payload(instruction),
                "output_hash": digest_payload(body),
                "timestamp_utc": (now or datetime.now(UTC)).isoformat(),
            },
        },
        sort_keys=True,
    )


def open_reply(payload: str | None) -> tuple[str, dict[str, Any] | None, str]:
    """Return (body, attestation, problem). ``problem`` is a reason code or empty."""
    if payload is None:
        return "", None, "EMPTY_REPLY"
    try:
        data = json.loads(payload)
        if data.get(ENVELOPE_KEY) != "1":
            raise ValueError
        body, attestation = data["body"], data["attestation"]
        if not isinstance(body, str) or not isinstance(attestation, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        return payload, None, "UNATTESTED_REPLY"
    if attestation.get("output_hash") != digest_payload(body):
        return body, attestation, "REPLY_HASH_MISMATCH"
    return body, attestation, ""


# ---------- grid wrappers ----------


class SpecialistGrid:
    """Wraps the SuperNode grid: replies leave with an attestation attached."""

    def __init__(self, grid: Any, instruction: str, label: str, clock=None) -> None:
        self._grid, self._instruction, self._label = grid, instruction, label
        self._clock = clock or (lambda: datetime.now(UTC))
        self.replied = False

    def tools(self) -> list[dict[str, Any]]:
        return self._grid.tools()

    def call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        if tool_call["name"] != "push_reply_message":
            return self._grid.call(tool_call)
        arguments = tool_call["arguments"]
        arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        tool_call = dict(tool_call)
        delivery = tool_call.pop("delivery", "model_tool_call")  # local marker, never sent
        arguments["payload"] = wrap_reply(
            arguments["payload"], self._instruction, self._label, self._clock(), delivery
        )
        self.replied = True
        return self._grid.call({**tool_call, "arguments": json.dumps(arguments)})

    def reply_with_text(self, text: str) -> None:
        """Send the model's final text when it answered without calling push_reply_message."""
        _trace(f"specialist {self._label}: no push_reply_message call; forwarding final text")
        self.call({
            "name": "push_reply_message",
            "arguments": json.dumps({"payload": text}),
            "call_id": "sb-harness-fallback",
            "delivery": "harness_fallback",
        })


@dataclass
class OrchestratorGrid:
    """Wraps the SuperLink grid: records delegations and verified replies as events."""

    grid: Any
    recorder: Recorder
    problems: list[str] = field(default_factory=list)
    _delegations: dict[str, tuple[str, str]] = field(default_factory=dict)  # msg_id -> (node, evt)
    _started_event_id: str | None = None

    def tools(self) -> list[dict[str, Any]]:
        return self.grid.tools()

    def call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        arguments = tool_call["arguments"]
        arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        name = tool_call["name"]
        output_item = self.grid.call(tool_call)
        output = json.loads(output_item["output"])
        if name == "push_messages":
            self._record_delegations(arguments, output)
        elif name == "pull_messages":
            output = self._record_replies(output)
            output_item = {**output_item, "output": json.dumps(output, sort_keys=True)}
        return output_item

    def _record_delegations(self, arguments: dict[str, Any], output: dict[str, Any]) -> None:
        for message, result in zip(arguments["messages"], output["results"], strict=False):
            if result.get("message_id") is None:
                continue
            node = str(message["dst_node_id"])
            event = self.recorder.emit(
                ORCHESTRATOR,
                ORCHESTRATOR,
                EventType.TASK_DELEGATED,
                f"Delegated a subtask to SuperNode {node}",
                input_payload=message["payload"],
                parent_event_id=self._started_event_id,
            )
            self._delegations[result["message_id"]] = (node, event.event_id)

    def _record_replies(self, output: dict[str, Any]) -> dict[str, Any]:
        cleaned = []
        for reply in output["messages"]:
            node = str(reply["src_node_id"])
            agent_id = f"supernode-{node}"
            _, delegated_event = self._delegations.get(reply["reply_to_message_id"], (node, None))
            body, attestation, problem = open_reply(reply.get("payload"))
            if reply.get("error"):
                problem = problem or "REPLY_ERROR"
            if attestation is not None:
                input_hash = attestation.get("input_hash")
                output_hash = attestation.get("output_hash") if not problem else None
            else:
                input_hash, output_hash = None, digest_payload(body) if body else None
            if problem:
                self.problems.append(f"{agent_id}: {problem}")
            note = f" [PROBLEM: {problem}]" if problem else ""
            done = self.recorder.emit(
                agent_id,
                "specialist",
                EventType.ACTION_COMPLETED,
                f"SuperNode {node} completed its subtask{note}",
                input_hash=input_hash,
                output_hash=output_hash,
                parent_event_id=delegated_event,
            )
            self.recorder.emit(
                agent_id,
                "specialist",
                EventType.RESULT_PRODUCED,
                f"SuperNode {node} returned its result{note}",
                output_hash=output_hash,
                parent_event_id=done.event_id,
            )
            cleaned.append({**reply, "payload": body if not problem else None,
                            "error": problem or reply.get("error")})
        return {**output, "messages": cleaned}


# ---------- model loop ----------


def model_tools(grid_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Flower's extra ``output_schema`` key, which is not part of the OpenAI tool schema."""
    return [{k: v for k, v in tool.items() if k != "output_schema"} for tool in grid_tools]


def _trace(message: str) -> None:
    """Diagnostic line for the run log. Shapes and names only, never keys or payload text."""
    print(f"sb_trace: {message}", file=sys.stderr, flush=True)


def _item_shapes(items: list[Any]) -> list[str]:
    """Summarize Responses items as ``type[keys]`` so rejected requests can be diagnosed."""
    shapes = []
    for item in items:
        data = item if isinstance(item, dict) else item.model_dump(exclude_none=True)
        kind = data.get("type") or data.get("role", "?")
        shapes.append(f"{kind}[{','.join(sorted(data))}]")
    return shapes


def run_model_loop(
    client: Any,
    model: str,
    instructions: str,
    prompt: str,
    grid: Any,
    max_turns: int = 8,
) -> str:
    """Responses-API tool loop. Every function_call goes through ``grid.call``."""
    items: list[Any] = [{"role": "user", "content": prompt}]
    tools = model_tools(grid.tools())
    text = ""
    for turn in range(max_turns):
        # The last turn offers no tools, so the model has to answer with what it has.
        last = turn == max_turns - 1
        turn_tools = [] if last else tools
        turn_instructions = instructions + (FINAL_TURN_NOTE if last else "")
        sent = _item_shapes(items)
        names = [t.get("name") for t in turn_tools]
        _trace(f"turn={turn} model={model} tools={names} input={sent}")
        try:
            response = client.responses.create(
                model=model,
                instructions=turn_instructions,
                input=items,
                tools=turn_tools,
                reasoning={"effort": "low"},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                timeout=MODEL_CALL_TIMEOUT_S,  # a stuck call fails fast instead of hanging
            )
        except Exception as exc:
            _trace(f"turn={turn} rejected: {type(exc).__name__}: {exc}")
            raise
        _trace(f"turn={turn} output={_item_shapes(response.output)}")
        calls = [item for item in response.output if item.type == "function_call"]
        text = getattr(response, "output_text", "") or text
        if not calls:
            return text
        items.extend(item.model_dump(exclude_none=True) for item in response.output)
        for item in calls:
            items.append(
                grid.call({"name": item.name, "arguments": item.arguments, "call_id": item.call_id})
            )
    return text or "Stopped: turn limit reached before a final answer."


# ---------- roles ----------


def run_specialist(session: Any, client: Any, model: str, label: str = "specialist") -> str:
    grid = SpecialistGrid(session.grid, session.prompt, label)
    text = run_model_loop(client, model, SPECIALIST_INSTRUCTIONS, session.prompt, grid)
    if not grid.replied and text:
        grid.reply_with_text(text)
    return text


def _message_ids(output_item: dict[str, Any]) -> list[str]:
    results = json.loads(output_item["output"])["results"]
    return [r["message_id"] for r in results if r.get("message_id")]


def _collect(grid: OrchestratorGrid, ids: list[str], wait_s: float) -> dict[str, str | None]:
    """Pull until every id has a reply or the wait runs out. Returns reply bodies by id."""
    bodies: dict[str, str | None] = {}
    pending, deadline = list(ids), time.monotonic() + wait_s
    while pending:
        remaining = max(0.0, deadline - time.monotonic())
        output = json.loads(grid.call({
            "name": "pull_messages",
            "arguments": json.dumps({"message_ids": pending, "timeout": min(30.0, remaining)}),
            "call_id": f"sb-scripted-pull-{uuid.uuid4().hex[:8]}",
        })["output"])
        for reply in output["messages"]:
            bodies[reply["reply_to_message_id"]] = reply.get("payload")
        pending = [i for i in pending if i not in bodies]
        if remaining <= 0:
            break
    return bodies


def run_scripted_delegation(grid: OrchestratorGrid, prompt: str) -> str:
    """Delegate fixed subtasks, then ask one SuperNode to arbitrate PAY or HOLD."""
    nodes_out = json.loads(grid.call({
        "name": "get_nodes", "arguments": json.dumps({"sample_size": None}),
        "call_id": "sb-scripted-nodes",
    })["output"])
    nodes = [str(n["id"]) for n in nodes_out.get("nodes", [])]
    _trace(f"scripted nodes={nodes}")
    if not nodes:
        return f"No SuperNodes available.\nRecommendation: {NO_DECISION}"

    messages = [
        {"dst_node_id": nodes[i % len(nodes)], "payload": f"{task}\n\nRequest: {prompt}",
         "reply_to_message_id": None}
        for i, (_, task) in enumerate(SCRIPTED_SUBTASKS)
    ]
    ids = _message_ids(grid.call({
        "name": "push_messages", "arguments": json.dumps({"messages": messages}),
        "call_id": "sb-scripted-push",
    }))
    _trace(f"scripted pushed={len(ids)}/{len(messages)}")
    bodies = _collect(grid, ids, SCRIPTED_WAIT_S)
    _trace(f"scripted replies={sum(1 for i in ids if bodies.get(i))}/{len(ids)}")
    findings = [
        f"- {label}: {bodies.get(msg_id) or 'no reply'}"
        for (label, _), msg_id in zip(SCRIPTED_SUBTASKS, ids, strict=False)
    ]

    arbiter_task = (
        "You are the arbiter for an HOA payment review. Based only on the findings below, "
        "answer with PAY or HOLD followed by a one-sentence reason. Never authorize payment; "
        f"two board members approve separately.\n\nRequest: {prompt}\n\nFindings:\n"
        + "\n".join(findings)
    )
    arbiter_ids = _message_ids(grid.call({
        "name": "push_messages",
        "arguments": json.dumps({"messages": [
            {"dst_node_id": nodes[0], "payload": arbiter_task, "reply_to_message_id": None}
        ]}),
        "call_id": "sb-scripted-arbiter",
    }))
    decision = None
    if arbiter_ids:
        decision = _collect(grid, arbiter_ids, ARBITER_WAIT_S).get(arbiter_ids[0])
    if not decision or not re.search(r"\b(PAY|HOLD)\b", decision):
        decision = NO_DECISION
    return "Findings:\n" + "\n".join(findings) + f"\nRecommendation: {decision.strip()}"


def run_orchestrator(
    session: Any,
    client: Any,
    model: str,
    ledger: Any = None,
    out_dir: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run the orchestrator, record the trace, and finalize only if the audit passes."""
    ledger = ledger if ledger is not None else SimulatedLedger()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    recorder = Recorder(run_id, ledger, clock=clock)
    started = recorder.emit(
        ORCHESTRATOR, ORCHESTRATOR, EventType.RUN_STARTED, "Run started",
        input_payload=session.prompt,
    )
    grid = OrchestratorGrid(session.grid, recorder)
    grid._started_event_id = started.event_id

    orchestration, note = "model", ""
    try:
        report = run_model_loop(
            client, model, ORCHESTRATOR_INSTRUCTIONS, session.prompt, grid,
            max_turns=ORCHESTRATOR_MAX_TURNS,
        )
    except openai.APIError as error:
        if grid._delegations:
            raise  # work is already out; re-delegating would double it
        orchestration = "scripted"
        note = f"orchestrator model unavailable ({type(error).__name__}); scripted delegation"
        _trace(note)
        report = run_scripted_delegation(grid, session.prompt)
    summary = "Combined specialist results" + (f" ({note})" if note else "")
    recorder.emit(
        ORCHESTRATOR, ORCHESTRATOR, EventType.RESULT_PRODUCED, summary,
        output_payload=report,
    )
    ledger.mine_demo_block()
    recorder.confirm()

    status, detail = "FINALIZED", ""
    try:
        if grid.problems:
            detail = "; ".join(grid.problems)
            raise ValueError("specialist replies failed verification")
        finalize_run(recorder, ledger)
        ledger.mine_demo_block()
        recorder.confirm()
    except (AuditFailedError, ValueError) as error:
        status = "NOT_FINALIZED"
        detail = detail or str(error)
    audit = audit_run(recorder.events, ledger)

    if out_dir is not None:
        try:
            save_log(out_dir / run_id / "events.jsonl", recorder.events)
            if isinstance(ledger, SimulatedLedger):
                ledger.path = out_dir / run_id / "ledger.json"
                ledger._save()
        except OSError:
            pass  # logging to disk is best-effort; the audit above already ran

    return {
        "schema": "flower-accountability/v1",
        "run_id": run_id,
        "status": status,
        "detail": detail,
        "human_approval_required": True,
        "ledger": getattr(ledger, "name", "unknown"),
        "audit": audit.render(),
        "events": [
            {"agent": e.agent_id, "type": e.event_type.value,
             "event_hash": e.event_hash, "txid": e.bitcoin_txid, "block": e.block_height}
            for e in recorder.events
        ],
        "report": report,
        "orchestration": orchestration,
        "orchestration_note": note,
    }
