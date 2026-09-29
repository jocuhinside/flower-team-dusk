# Copyright 2026 Flower Labs GmbH. All Rights Reserved.
"""Workflow for Endeavor Agent connector calls and response streaming."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from flwr.agentapp import AgentSession
from flwr.app import ConfigRecord, Context
from openai import OpenAI, Stream
from openai.types.responses import Response, ResponseStreamEvent

from endeavor_agent.prompts import build_tool_instructions

_MODEL = "flwrlabs/endeavor-1.0"
_WEB_SEARCH_TOOL = "web_search"
_START_AUTOMATION_TOOL = "start_automation"
_ATTIO_LIST_MEETINGS_TOOL = "attio_list_meetings"
_ATTIO_GET_WORKSPACE_MEMBER_TOOL = "attio_get_workspace_member"
_MAX_CONNECTOR_ROUNDS = 4
_OAUTH_CONNECTOR_REFS = ("attio", "github", "notion", "slack")
_AUTOMATION_STATE_KEY = "endeavor_agent.automation"
_SUCCESS_TERMINAL_EVENTS = {"response.completed", "response.incomplete"}


def runtime_client() -> OpenAI:
    """Create an OpenAI client authenticated against Flower Runtime."""
    return OpenAI(
        base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
        api_key=os.environ["FLWR_RUNTIME_API_KEY"],
        max_retries=0,
    )


def build_history(agent: AgentSession, context: Context) -> list[dict[str, Any]]:
    """Build the conversation from the current run-series trace."""
    prompt = agent.prompt
    conversation = []
    current_prompt_seen = False

    for trace_event in agent.events.get_trace():
        data = cast(dict[str, Any], trace_event["data"])
        if data.get("type") == "message":
            role = data.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = _message_text(data["content"])
            conversation.append({"type": "message", "role": role, "content": content})
            if (
                role == "user"
                and trace_event.get("run_id") == context.run_id
                and content.strip() == prompt
            ):
                current_prompt_seen = True
            continue

        if data.get("type") != "response.completed":
            continue
        for item in data["response"]["output"]:
            if item.get("type") != "message" or item.get("role") != "assistant":
                continue
            conversation.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": _message_text(item["content"]),
                }
            )

    if not current_prompt_seen:
        conversation.append({"type": "message", "role": "user", "content": prompt})
    return conversation


def available_connector_tools(
    agent: AgentSession, context: Context
) -> list[dict[str, Any]]:
    """Load the tools available for this run."""
    connector_refs = [_WEB_SEARCH_TOOL, *_OAUTH_CONNECTOR_REFS]
    if _AUTOMATION_STATE_KEY not in context.state:
        connector_refs.append(_START_AUTOMATION_TOOL)
    return _strengthen_attio_schemas(agent.connectors.tools(connector_refs))


def _strengthen_attio_schemas(
    connector_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply temporary Attio constraints until the connector publishes them."""
    tools = [deepcopy(tool) for tool in connector_tools]
    for tool in tools:
        if tool.get("name") == _ATTIO_GET_WORKSPACE_MEMBER_TOOL:
            tool["description"] = (
                f"{tool.get('description', '')} A workspace-member UUID identifies an "
                "actor, not a people record, and must never be used as an Attio record ID."
            ).strip()
        if tool.get("name") != _ATTIO_LIST_MEETINGS_TOOL:
            continue

        parameters = tool["parameters"]
        properties = parameters["properties"]
        properties["limit"]["maximum"] = 200
        for name in ("cursor", "participants", "timezone"):
            properties[name]["minLength"] = 1
        properties["cursor"]["description"] = (
            "Opaque pagination.next_cursor from a previous response using the same "
            "filters. Omit this field for the first request."
        )
        properties["linked_record_id"]["description"] = (
            "Attio record UUID. This is not a workspace-member UUID. Provide only when "
            "the record ID is known, together with linked_object."
        )
        parameters["dependentRequired"] = {
            "linked_object": ["linked_record_id"],
            "linked_record_id": ["linked_object"],
        }
        tool["description"] = (
            "List meetings in the authenticated Attio workspace. For the latest past "
            "meeting, use start_desc and starts_before, and omit unknown linked-record "
            "or participant filters. Results expose created_by_actor.id as a "
            "workspace-member UUID, which may be compared with the ID returned by "
            "attio_identify."
        )
    return tools


def run_connector_loop(
    agent: AgentSession,
    context: Context,
    conversation: list[dict[str, Any]],
    connector_tools: list[dict[str, Any]],
    *,
    client: OpenAI,
) -> list[dict[str, Any]]:
    """Let Endeavor call connectors until it has enough context to answer."""
    tool_names = {
        tool["name"] for tool in connector_tools if isinstance(tool.get("name"), str)
    }
    tool_history: list[dict[str, Any]] = []
    instructions = build_tool_instructions(datetime.now(UTC).isoformat())

    for _ in range(_MAX_CONNECTOR_ROUNDS):
        response = cast(
            Response,
            client.responses.create(
                model=_MODEL,
                input=[*conversation, *tool_history],
                stream=False,
                instructions=instructions,
                tools=connector_tools,
                tool_choice="auto",
            ),
        )
        response_data = cast(dict[str, Any], response.to_dict())
        tool_calls = [
            _normalize_connector_call(dict(item))
            for item in response_data["output"]
            if item["type"] == "function_call" and item["name"] in tool_names
        ]
        if not tool_calls:
            break

        automation_count = sum(
            call["name"] == _START_AUTOMATION_TOOL for call in tool_calls
        )
        if automation_count > 1:
            raise RuntimeError("The model returned more than one automation request.")

        tool_outputs = _execute_connector_calls(
            agent, context, tool_calls, tool_history
        )
        tool_history.extend([*tool_calls, *tool_outputs])
        if automation_count:
            break

    return tool_history


def _execute_connector_calls(
    agent: AgentSession,
    context: Context,
    tool_calls: list[dict[str, Any]],
    tool_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute one round of connector calls and capture recoverable errors."""
    tool_outputs = []
    validation_history = [*tool_history, *tool_calls]
    for tool_call in tool_calls:
        validation_error = _connector_call_validation_error(
            tool_call, validation_history
        )
        if validation_error:
            tool_output = {
                "type": "function_call_output",
                "call_id": tool_call["call_id"],
                "output": json.dumps({"error": validation_error}),
            }
        else:
            try:
                tool_output = agent.connectors.call(tool_call)
            except RuntimeError as exc:
                tool_output = {
                    "type": "function_call_output",
                    "call_id": tool_call["call_id"],
                    "output": json.dumps({"error": str(exc)}),
                }
            else:
                if tool_call["name"] == _START_AUTOMATION_TOOL:
                    context.state[_AUTOMATION_STATE_KEY] = ConfigRecord(
                        {"created": True}
                    )
        tool_outputs.append(tool_output)
    return tool_outputs


def _normalize_connector_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Omit empty optional Attio meeting filters generated by the model."""
    if tool_call["name"] != _ATTIO_LIST_MEETINGS_TOOL:
        return tool_call
    arguments = json.loads(tool_call["arguments"])
    for name in (
        "cursor",
        "linked_object",
        "linked_record_id",
        "participants",
        "sort",
        "ends_from",
        "starts_before",
        "timezone",
    ):
        value = arguments.get(name)
        if value is None or isinstance(value, str) and not value.strip():
            arguments.pop(name, None)
    return {**tool_call, "arguments": json.dumps(arguments, separators=(",", ":"))}


def _connector_call_validation_error(
    tool_call: dict[str, Any], tool_history: list[dict[str, Any]]
) -> str | None:
    """Reject unsafe Attio meeting filters before invoking the connector."""
    if tool_call["name"] != _ATTIO_LIST_MEETINGS_TOOL:
        return None
    arguments = json.loads(tool_call["arguments"])
    linked_object = arguments.get("linked_object")
    linked_record_id = arguments.get("linked_record_id")
    if bool(linked_object) != bool(linked_record_id):
        return "linked_object and linked_record_id must be provided together."
    if not linked_record_id:
        return None
    try:
        record_id = UUID(linked_record_id)
    except (TypeError, ValueError):
        return "linked_record_id must be a valid Attio record UUID."
    if record_id.int == 0:
        return (
            "linked_record_id is a nil UUID. Omit unknown optional filters; do not "
            "invent identifiers."
        )
    workspace_member_ids = {
        json.loads(item["arguments"]).get("workspace_member_id")
        for item in tool_history
        if item.get("type") == "function_call"
        and item.get("name") == _ATTIO_GET_WORKSPACE_MEMBER_TOOL
    }
    if linked_record_id in workspace_member_ids:
        return (
            "linked_record_id is a workspace-member UUID, not an Attio record UUID. "
            "Omit the linked-record filters and compare created_by_actor.id in the "
            "meeting results instead."
        )
    return None


def stream_final_answer(
    agent: AgentSession,
    conversation: list[dict[str, Any]],
    tool_history: list[dict[str, Any]],
    *,
    client: OpenAI,
) -> None:
    """Stream the user-visible answer after connector work is complete."""
    stream = cast(
        Stream[ResponseStreamEvent],
        client.responses.create(
            model=_MODEL,
            input=[*conversation, *tool_history],
            stream=True,
            instructions=build_tool_instructions(datetime.now(UTC).isoformat()),
        ),
    )
    response: dict[str, Any] | None = None
    with stream:
        for event in stream:
            event_payload = cast(dict[str, Any], event.to_dict())
            agent.events.emit(event_payload)
            if event.type == "response.failed":
                return
            if event.type in _SUCCESS_TERMINAL_EVENTS:
                completed_response = event_payload.get("response")
                if isinstance(completed_response, dict):
                    response = completed_response

    if response is None:
        raise RuntimeError("Runtime response stream ended without a successful result.")


def _message_text(content: Any) -> str:
    """Extract text from message content."""
    if isinstance(content, str):
        return content
    return "\n".join(
        part["text"]
        for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
