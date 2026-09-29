# Copyright 2026 Flower Labs GmbH. All Rights Reserved.
"""Focused tests for the Endeavor Agent workflow."""

import json
from typing import Any

from endeavor_agent.workflow import (
    available_connector_tools,
    build_history,
    run_connector_loop,
    stream_final_answer,
)

_USER_PROMPT = "Find the latest Flower news."


class _FakeContext:
    def __init__(self) -> None:
        self.run_id = 123
        self.state: dict[str, Any] = {}


class _FakeConnectors:
    def __init__(self, error: RuntimeError | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def tools(self, names: list[str]) -> list[dict[str, Any]]:
        return [{"type": "function", "name": name} for name in names]

    def call(self, tool_call: dict[str, Any]) -> dict[str, str]:
        self.calls.append(tool_call)
        if self.error is not None:
            raise self.error
        return {
            "type": "function_call_output",
            "call_id": tool_call["call_id"],
            "output": "Connector result.",
        }


class _FakeEvents:
    def __init__(self, trace: list[dict[str, Any]] | None = None) -> None:
        self.trace = trace or []
        self.emitted: list[dict[str, Any]] = []

    def get_trace(self) -> list[dict[str, Any]]:
        return self.trace

    def emit(self, event: dict[str, Any]) -> None:
        self.emitted.append(event)


class _FakeAgent:
    def __init__(
        self,
        connectors: _FakeConnectors,
        trace: list[dict[str, Any]] | None = None,
        prompt: str = _USER_PROMPT,
    ) -> None:
        self.connectors = connectors
        self.events = _FakeEvents(trace)
        self.prompt = prompt


class _FakeSdkObject:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.type = payload.get("type")

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class _FakeStream:
    def __init__(self, events: list[_FakeSdkObject]) -> None:
        self.events = events
        self.is_closed = False

    def __iter__(self):
        return iter(self.events)

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        self.is_closed = True


class _FakeResponses:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.streams: list[_FakeStream] = []

    def create(self, **request: Any) -> object:
        self.requests.append(request)
        response = self.responses.pop(0)
        if request["stream"]:
            events = (
                [_FakeSdkObject({"type": "response.failed", "response": response})]
                if response.get("status") == "failed"
                else [
                    _FakeSdkObject(
                        {"type": "response.output_text.delta", "delta": "Done."}
                    ),
                    _FakeSdkObject(
                        {"type": "response.completed", "response": response}
                    ),
                ]
            )
            stream = _FakeStream(events)
            self.streams.append(stream)
            return stream
        return _FakeSdkObject(response)


class _FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = _FakeResponses(responses)


def test_build_history_rebuilds_messages_from_trace() -> None:
    context = _FakeContext()
    trace = [
        {
            "run_id": 100,
            "data": {"type": "message", "role": "user", "content": "Earlier"},
        },
        {
            "run_id": 100,
            "data": {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "Previous answer."}
                            ],
                        }
                    ]
                },
            },
        },
        {
            "run_id": context.run_id,
            "data": {
                "type": "message",
                "role": "user",
                "content": _USER_PROMPT,
            },
        },
    ]
    agent = _FakeAgent(_FakeConnectors(), trace)

    conversation = build_history(agent, context)  # type: ignore[arg-type]

    assert conversation == [
        {"type": "message", "role": "user", "content": "Earlier"},
        {"type": "message", "role": "assistant", "content": "Previous answer."},
        {"type": "message", "role": "user", "content": _USER_PROMPT},
    ]


def test_build_history_appends_agent_session_prompt() -> None:
    context = _FakeContext()
    agent = _FakeAgent(_FakeConnectors(), prompt="Prompt from AgentSession.")

    conversation = build_history(agent, context)  # type: ignore[arg-type]

    assert conversation == [
        {
            "type": "message",
            "role": "user",
            "content": "Prompt from AgentSession.",
        }
    ]


def test_workflow_executes_tool_calls_and_streams_final_answer() -> None:
    context = _FakeContext()
    connectors = _FakeConnectors()
    agent = _FakeAgent(connectors)
    tool_call = {
        "type": "function_call",
        "name": "web_search",
        "call_id": "call-search",
        "arguments": json.dumps({"query": "latest Flower news"}),
    }
    final_response = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            }
        ]
    }
    client = _FakeClient(
        [
            {"output": [tool_call]},
            {"output": [{"type": "reasoning"}]},
            final_response,
        ]
    )
    conversation = [{"type": "message", "role": "user", "content": _USER_PROMPT}]

    tools = available_connector_tools(agent, context)  # type: ignore[arg-type]
    tool_history = run_connector_loop(  # type: ignore[arg-type]
        agent,
        context,
        conversation,
        tools,
        client=client,  # type: ignore[arg-type]
    )
    stream_final_answer(  # type: ignore[arg-type]
        agent,
        conversation,
        tool_history,
        client=client,  # type: ignore[arg-type]
    )

    assert [tool["name"] for tool in tools] == [
        "web_search",
        "attio",
        "github",
        "notion",
        "slack",
        "start_automation",
    ]
    assert connectors.calls == [tool_call]
    assert client.responses.requests[0]["tool_choice"] == "auto"
    assert client.responses.requests[0]["stream"] is False
    assert client.responses.requests[1]["input"][-2:] == [
        tool_call,
        {
            "type": "function_call_output",
            "call_id": "call-search",
            "output": "Connector result.",
        },
    ]
    final_request = client.responses.requests[2]
    assert final_request["input"] == client.responses.requests[1]["input"]
    assert final_request["stream"] is True
    assert "tools" not in final_request
    assert "tool_choice" not in final_request
    assert [event["type"] for event in agent.events.emitted] == [
        "response.output_text.delta",
        "response.completed",
    ]


def test_failed_response_ends_stream_without_raising() -> None:
    agent = _FakeAgent(_FakeConnectors())
    failed_response = {
        "status": "failed",
        "error": {"code": "server_error", "message": "Request failed."},
    }
    client = _FakeClient([failed_response])

    stream_final_answer(  # type: ignore[arg-type]
        agent,
        [{"type": "message", "role": "user", "content": _USER_PROMPT}],
        [],
        client=client,  # type: ignore[arg-type]
    )

    assert agent.events.emitted == [
        {"type": "response.failed", "response": failed_response}
    ]
    assert client.responses.streams[0].is_closed


def test_connector_error_is_returned_to_model() -> None:
    context = _FakeContext()
    connectors = _FakeConnectors(RuntimeError("Connector is unavailable."))
    agent = _FakeAgent(connectors)
    tool_call = {
        "type": "function_call",
        "name": "notion",
        "call_id": "call-notion",
        "arguments": "{}",
    }
    client = _FakeClient(
        [
            {"output": [tool_call]},
            {"output": [{"type": "reasoning"}]},
        ]
    )
    conversation = [{"type": "message", "role": "user", "content": _USER_PROMPT}]

    tools = available_connector_tools(agent, context)  # type: ignore[arg-type]
    tool_history = run_connector_loop(  # type: ignore[arg-type]
        agent,
        context,
        conversation,
        tools,
        client=client,  # type: ignore[arg-type]
    )

    assert json.loads(tool_history[-1]["output"]) == {
        "error": "Connector is unavailable."
    }


def test_automation_is_available_once() -> None:
    context = _FakeContext()
    connectors = _FakeConnectors()
    agent = _FakeAgent(connectors)
    tool_call = {
        "type": "function_call",
        "name": "start_automation",
        "call_id": "call-automation",
        "arguments": json.dumps(
            {"input": "Summarize Flower news.", "start_at": "2026-09-20T09:00:00Z"}
        ),
    }
    client = _FakeClient([{"output": [tool_call]}])
    conversation = [{"type": "message", "role": "user", "content": _USER_PROMPT}]

    tools = available_connector_tools(agent, context)  # type: ignore[arg-type]
    run_connector_loop(  # type: ignore[arg-type]
        agent,
        context,
        conversation,
        tools,
        client=client,  # type: ignore[arg-type]
    )

    assert context.state["endeavor_agent.automation"] == {"created": True}
    next_tools = available_connector_tools(agent, context)  # type: ignore[arg-type]
    assert "start_automation" not in {tool["name"] for tool in next_tools}
