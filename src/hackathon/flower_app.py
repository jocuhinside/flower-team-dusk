"""Flower AgentApp: accountable multi-agent run on SuperGrid.

The same AgentApp runs on both sides. On the SuperLink it acts as the orchestrator
(delegates, collects, audits, finalizes only after a verified trace). On a
SuperNode it acts as a specialist (does one subtask, replies with a hash-only
attestation). Nothing here executes or authorizes a transaction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context
from openai import OpenAI

from .config import SponsorConfig
from .flower_agents import run_orchestrator, run_specialist
from .ledger import SimulatedLedger, ledger_from_environment

app = AgentApp()


def _ledger():
    if os.environ.get("BITCOIN_RPC_USER") and os.environ.get("BITCOIN_RPC_PASSWORD"):
        return ledger_from_environment()
    return SimulatedLedger()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    del context
    config = SponsorConfig.from_environment()
    client = OpenAI(api_key=config.api_key, base_url=config.base_url, max_retries=0)
    tool_names = {tool["name"] for tool in agent.grid.tools()}

    if "push_messages" in tool_names:
        result = run_orchestrator(
            agent,
            client,
            config.model,
            ledger=_ledger(),
            out_dir=Path(os.environ.get("ACCOUNTABILITY_DIR", "accountability-runs")),
        )
        text = json.dumps(result, sort_keys=True)
    else:
        label = os.environ.get("AGENT_LABEL", "specialist")
        text = run_specialist(agent, client, config.model, label=label)

    agent.events.emit({"type": "response.output_text.done", "text": text})
