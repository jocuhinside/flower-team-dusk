<!-- Copyright 2026 Flower Labs GmbH. All Rights Reserved. -->
# Endeavor Agent

This Flower App sends each prompt to `flwrlabs/endeavor-1.0`. Private
tool-selection calls remain hidden, while the final model response streams to
the frontend.

Install `uv` and log in to SuperGrid:

```bash
uvx flwr login supergrid
```

Start Flower Chat from the repository root:

```bash
uvx flwr chat
```

Select your personal federation and load the local AgentApp in Flower Chat.
Replace `YOUR_FLOWER_USERNAME` with your Flower account name:

```text
/federation @YOUR_FLOWER_USERNAME/personal
/load agent/endeavor-agent
Hello
```

The user prompt comes from `AgentSession.prompt`.

## Tools and authentication

Web search is available without account authentication. The agent can also use
OAuth-connected Attio, GitHub, Notion, and Slack accounts selected for the
current run. Connect the required account in SuperGrid before starting the run.
SuperGrid keeps the OAuth credentials and executes connector calls on the
agent's behalf. The credentials are not exposed to the AgentApp.

An explicit future or recurring request can also create an automation. The
request must include a start time and, for recurring work, a cadence.
