# Copyright 2026 Flower Labs GmbH. All Rights Reserved.
"""The Endeavor Agent app."""

from __future__ import annotations

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from endeavor_agent.workflow import (
    available_connector_tools,
    build_history,
    run_connector_loop,
    runtime_client,
    stream_final_answer,
)

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Run one Endeavor Agent turn."""
    conversation = build_history(agent, context)
    client = runtime_client()
    connector_tools = available_connector_tools(agent, context)
    tool_history = run_connector_loop(
        agent, context, conversation, connector_tools, client=client
    )
    stream_final_answer(agent, conversation, tool_history, client=client)
