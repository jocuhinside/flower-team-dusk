# Collaborative Agent Transaction Review

This project keeps responsibilities deliberately small:

- **GitHub/Git** is the source-control and collaboration layer.
- **Flower** is the agent runtime/federation layer.
- **Pydantic** validates every object crossing the trust boundary.
- A named human must confirm the SHA-256 of the exact review package before
  an evidence manifest can be finalized.

The current milestone contains no Neo4j, Bitcoin, NFC, custody hardware, or
other stretch integrations.

## Setup

```bash
uv sync --python 3.12
cp .env.example .env
```

Populate `.env` only on the machine that will call the sponsor's
OpenAI-compatible API. Never commit `.env` or paste its key into chat.

## Verify the core

```bash
uv run ruff check .
uv run pytest
uv run flwr build
uv run hackathon config
uv run hackathon smoke
```

The smoke test is deterministic and does not spend sponsor API quota. It
creates one transaction, runs the collector/policy/red-team review logic,
confirms the exact review hash as a simulated human, then emits and verifies a
canonical evidence manifest and its SHA-256.

## Flower runtime

The Flower `AgentApp` lives at `hackathon.flower_app:app`. It uses
`SPONSOR_API_KEY`, `SPONSOR_BASE_URL`, and `SPONSOR_MODEL` when launched through
a Flower Agent runtime. The application never prints the secret value.

The offline smoke path should pass before attempting a live sponsor call.
