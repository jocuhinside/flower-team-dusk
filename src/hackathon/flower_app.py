"""Flower AgentApp using the sponsor's OpenAI-compatible endpoint."""

from __future__ import annotations

import json

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context
from openai import OpenAI

from .config import SponsorConfig

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Run the collaborative review through Flower; never execute a transaction."""
    del context
    config = SponsorConfig.from_environment()
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, max_retries=0)
    response = client.responses.create(
        model=config.model,
        input=(
            "Review this proposed transaction as collector, policy, and red-team roles. "
            "Return a concise report only; do not execute or authorize any transaction. "
            "State that a named human must confirm the exact SHA-256 before finalization.\n"
            f"Proposal: {agent.prompt}"
        ),
    )
    agent.events.emit(
        {
            "type": "response.output_text.done",
            "text": json.dumps(
                {
                    "schema": "flower-collaboration/v1",
                    "human_approval_required": True,
                    "report": response.output_text,
                },
                sort_keys=True,
            ),
        }
    )
