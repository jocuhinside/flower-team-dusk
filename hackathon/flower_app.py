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


def _summary(result: dict) -> str:
    """Plain-text outcome built from the signed result, for when no model can summarize."""
    audit_line = (result.get("audit") or "").strip().splitlines()[-1:] or [""]
    return (
        f"Status: {result['status']}. Run ID: {result['run_id']}. "
        f"Ledger: {result['ledger']}. Audit: {audit_line[0]}. "
        f"Orchestration: {result['orchestration_note']}.\n\n{result['report']}"
    )


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
        if result.get("orchestration") == "scripted":
            # The orchestrator's model endpoint just failed; don't spend another call on it.
            agent.events.emit({"type": "response.output_text.done", "text": _summary(result)})
            return
    else:
        label = os.environ.get("AGENT_LABEL", "specialist")
        # Specialists retry transient provider errors (e.g. a dropped connection to the model
        # endpoint); the orchestrator does not, so an unreachable endpoint falls back quickly.
        text = run_specialist(agent, client.with_options(max_retries=2), config.model, label=label)

    if "push_messages" in tool_names:
        # Same pattern as Flower's reference app: the visible answer is a streamed
        # Responses call with no tools, and every event is forwarded to the chat.
        # The full signed result stays in `text`; if streaming fails we fall back to it.
        try:
            stream = client.responses.create(
                model=config.model,
                instructions=(
                    "You report the outcome of an accountable payment review. In under 120 "
                    "words, state the status, run_id, ledger, audit line and the report's "
                    "PAY or HOLD recommendation from the JSON below. Do not invent facts."
                ),
                input=text,
                stream=True,
                timeout=60,
            )
            for event in stream:
                agent.events.emit(event.to_dict())
            return
        except Exception:  # noqa: BLE001 - best-effort presentation only
            pass
    agent.events.emit({"type": "response.output_text.done", "text": text})
