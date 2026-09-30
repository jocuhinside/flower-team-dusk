from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from hackathon.auditor import audit_run
from hackathon.events import load_log
from hackathon.flower_agents import (
    SpecialistGrid,
    model_tools,
    open_reply,
    run_model_loop,
    run_orchestrator,
    run_specialist,
    wrap_reply,
)
from hackathon.ledger import SimulatedLedger

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def clock():
    n = {"i": 0}

    def tick():
        n["i"] += 1
        return NOW.replace(second=n["i"] % 60, minute=n["i"] // 60)

    return tick


class Item(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


def call_item(name, args, call_id):
    return Item(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


class ScriptedClient:
    """Fake Responses API that plays a fixed list of turns."""

    def __init__(self, turns):
        self.turns, self.seen_tools = list(turns), []
        self.responses = self

    def create(self, **kwargs):
        self.seen_tools.append(kwargs["tools"])
        output, text = self.turns.pop(0)
        return SimpleNamespace(output=output, output_text=text)


class FakeOrchestratorGrid:
    """SuperLink-side grid; specialists' replies are built with the real wrapper."""

    def __init__(self, tamper=False, unattested=False):
        self.tamper, self.unattested, self.sent = tamper, unattested, []

    def tools(self):
        return [
            {"type": "function", "name": n, "parameters": {}, "output_schema": {"x": 1}}
            for n in ("get_nodes", "push_messages", "pull_messages")
        ]

    def call(self, tool_call):
        args = json.loads(tool_call["arguments"])
        name = tool_call["name"]
        if name == "get_nodes":
            out = {"nodes": [{"id": "11", "name": "a", "location": None},
                             {"id": "22", "name": "b", "location": None}], "num_available": 2}
        elif name == "push_messages":
            self.sent = args["messages"]
            out = {"results": [{"message_id": f"m{i}", "error": None}
                               for i, _ in enumerate(args["messages"])]}
        else:
            replies = []
            for i, message in enumerate(self.sent):
                body = f"answer {i}"
                payload = wrap_reply(body, message["payload"], f"n{i}", NOW)
                if self.unattested:
                    payload = body
                elif self.tamper and i == 1:
                    payload = payload.replace("answer 1", "changed after attestation")
                replies.append({"message_id": f"r{i}", "reply_to_message_id": f"m{i}",
                                "src_node_id": message["dst_node_id"], "payload": payload,
                                "error": None})
            out = {"messages": replies, "pending_message_ids": []}
        return {"type": "function_call_output", "call_id": tool_call["call_id"],
                "output": json.dumps(out)}


def orchestrator_turns():
    push = {"messages": [
        {"dst_node_id": "11", "payload": "check amount", "reply_to_message_id": None},
        {"dst_node_id": "22", "payload": "check destination", "reply_to_message_id": None},
    ]}
    return [
        ([call_item("get_nodes", {"sample_size": None}, "c1")], ""),
        ([call_item("push_messages", push, "c2")], ""),
        ([call_item("pull_messages", {"message_ids": ["m0", "m1"], "timeout": 5}, "c3")], ""),
        ([Item(type="message")], "Final report: looks fine. Human must confirm."),
    ]


def run(tmp_path, **grid_kwargs):
    session = SimpleNamespace(prompt="Review a purchase", grid=FakeOrchestratorGrid(**grid_kwargs))
    client = ScriptedClient(orchestrator_turns())
    ledger = SimulatedLedger()
    result = run_orchestrator(session, client, "m", ledger, tmp_path, clock())
    return result, client, ledger


def test_orchestrator_run_is_recorded_audited_and_finalized(tmp_path: Path) -> None:
    result, client, ledger = run(tmp_path)
    assert result["status"] == "FINALIZED" and result["human_approval_required"] is True
    types = [e["type"] for e in result["events"]]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINALIZED"
    assert types.count("TASK_DELEGATED") == 2
    agents = {e["agent"] for e in result["events"]}
    assert {"orchestrator", "supernode-11", "supernode-22", "auditor"} <= agents
    saved = load_log(tmp_path / result["run_id"] / "events.jsonl")
    assert audit_run(saved, ledger).passed
    assert all("output_schema" not in tool for tool in client.seen_tools[0])


def test_no_payload_text_in_recorded_events(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path)
    log = (tmp_path / result["run_id"] / "events.jsonl").read_text()
    assert "check amount" not in log and "answer 0" not in log


def test_tampered_reply_blocks_finalization(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path, tamper=True)
    assert result["status"] == "NOT_FINALIZED"
    assert "REPLY_HASH_MISMATCH" in result["detail"]
    assert "RUN_FINALIZED" not in [e["type"] for e in result["events"]]


def test_unattested_reply_blocks_finalization(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path, unattested=True)
    assert result["status"] == "NOT_FINALIZED"
    assert "UNATTESTED_REPLY" in result["detail"]


def test_reply_envelope_round_trip_and_detection() -> None:
    payload = wrap_reply("hello", "do a thing", "n1", NOW)
    assert open_reply(payload)[2] == ""
    assert open_reply(payload.replace("hello", "HELLO"))[2] == "REPLY_HASH_MISMATCH"
    assert open_reply("plain text")[2] == "UNATTESTED_REPLY"
    assert open_reply(None)[2] == "EMPTY_REPLY"


class FakeSpecialistGrid:
    def __init__(self):
        self.calls = []

    def tools(self):
        return [{"type": "function", "name": "push_reply_message", "parameters": {}}]

    def call(self, tool_call):
        self.calls.append(tool_call)
        return {"type": "function_call_output", "call_id": tool_call["call_id"], "output": "{}"}


def test_specialist_reply_is_attested_before_leaving() -> None:
    grid = FakeSpecialistGrid()
    session = SimpleNamespace(prompt="Check destination policy", grid=grid)
    client = ScriptedClient([
        ([call_item("push_reply_message", {"payload": "policy ok"}, "c1")], ""),
        ([Item(type="message")], "done"),
    ])
    assert run_specialist(session, client, "m", label="safety") == "done"
    sent = json.loads(json.loads(grid.calls[0]["arguments"])["payload"])
    body, attestation, problem = open_reply(json.dumps(sent))
    assert body == "policy ok" and problem == "" and attestation["agent_label"] == "safety"


def test_specialist_text_answer_is_still_replied_once() -> None:
    grid = FakeSpecialistGrid()
    session = SimpleNamespace(prompt="Check vendor list", grid=grid)
    client = ScriptedClient([([Item(type="message")], "vendor approved")])
    run_specialist(session, client, "m", label="vendor")
    assert [c["name"] for c in grid.calls] == ["push_reply_message"]
    sent = json.loads(json.loads(grid.calls[0]["arguments"])["payload"])
    body, attestation, problem = open_reply(json.dumps(sent))
    assert body == "vendor approved" and problem == ""
    assert attestation["delivery"] == "harness_fallback"
    assert "delivery" not in grid.calls[0]


def test_specialist_tool_reply_is_not_sent_twice() -> None:
    grid = FakeSpecialistGrid()
    session = SimpleNamespace(prompt="Check budget", grid=grid)
    client = ScriptedClient([
        ([call_item("push_reply_message", {"payload": "within budget"}, "c1")], ""),
        ([Item(type="message")], "done"),
    ])
    run_specialist(session, client, "m", label="budget")
    assert len(grid.calls) == 1
    sent = json.loads(json.loads(grid.calls[0]["arguments"])["payload"])
    assert sent["attestation"]["delivery"] == "model_tool_call"


def test_last_turn_has_no_tools_so_the_model_must_answer() -> None:
    grid = FakeSpecialistGrid()
    turns = [([call_item("other", {}, f"c{i}")], "") for i in range(2)]
    client = ScriptedClient([*turns, ([Item(type="message")], "final")])
    assert run_model_loop(client, "m", "do it", "p", grid, max_turns=3) == "final"
    assert client.seen_tools[0] and client.seen_tools[-1] == []


def test_specialist_grid_passes_other_tools_through() -> None:
    grid = FakeSpecialistGrid()
    SpecialistGrid(grid, "x", "l").call({"name": "other", "arguments": "{}", "call_id": "c"})
    assert grid.calls[0]["name"] == "other"


def test_model_tools_strips_output_schema() -> None:
    assert model_tools([{"name": "a", "output_schema": {}}]) == [{"name": "a"}]
