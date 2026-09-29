# HOA Payment Accountability Chain (Team Dusk)

Flower agents review a vendor payment on SuperGrid: one extracts the invoice
fields, others check vendor, budget and reserve rules, and an arbiter recommends
PAY or HOLD. Two board members approve the exact review hash. Every handoff is
recorded as a hash-chained event, the evidence bundle is signed, and event hashes
can be anchored to a Bitcoin Core regtest node. An Auditor refuses to finalize a
run whose record does not verify.

Responsibilities are deliberately small:

- **Git/GitHub** is the source-control and collaboration layer.
- **Flower** is the agent runtime/federation layer (`AgentApp` at `hackathon.flower_app:app`).
- **Pydantic** validates every object crossing the trust boundary.
- Named humans must confirm the SHA-256 of the exact review package before an
  evidence manifest can be finalized.

Not in scope: Neo4j lineage, NFC, custody hardware, Sigstore/Cosign, per-agent keys.

## Setup

```bash
uv sync --python 3.12
cp .env.example .env
```

Populate `.env` only on the machine that will call the model endpoint. Never
commit `.env` or paste its key into chat. Required values: an API key, an
endpoint, and a model name (`SPONSOR_API_KEY` / `SPONSOR_BASE_URL` /
`AGENT_MODEL`, or `FLWR_MODEL_API_KEY` / `FLWR_MODEL_API_ENDPOINT` /
`SPONSOR_MODEL`). On SuperGrid the runtime supplies the key and endpoint
automatically and only the model name is needed.

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

The Flower `AgentApp` lives at `hackathon.flower_app:app`. The same app runs as
the orchestrator (SuperLink side) or a specialist (SuperNode side). Configuration
is read from the variables above; the application never prints a secret value.
Each SuperGrid task has a 5-minute timeout once running, so the chain must finish
inside it. The offline smoke path should pass before attempting a live model call.

Publishing to Flower Hub (public, cannot be removed once published): sign in as
the account named in `publisher` in `pyproject.toml`, then
`uv run flwr build`, `uv run flwr login supergrid`, `uv run flwr app publish .`.
Publish uploads every eligible file, tracked or not, after `.gitignore` is applied.

## Accountability chain demo (HOA vendor payment)

Offline demos (no keys or network needed):

```bash
uv run hackathon keygen            # once; keys/ is gitignored
uv run hackathon demo              # signed golden / tampered / tampered-rehashed bundles
uv run hackathon verify demo/golden --pubkey demo/keys/demo-signer.pub
uv run hackathon chain-demo        # hash-chained events on a SIMULATED ledger (not Bitcoin)
uv run hackathon audit-chain demo/chain/golden
uv run hackathon approve-ui --approvers 2   # two board members approve the exact hash
```

Bitcoin Core regtest (private test chain you run yourself): see `ansible/` and
`scripts/regtest_up.sh` (copy `ansible/inventory.example.ini` to `inventory.ini`,
which is gitignored), then `uv run hackathon chain-demo --ledger bitcoin`. The
Bitcoin adapter has been exercised against a fake RPC; a live-node run is not yet
confirmed. Use the simulated ledger otherwise and say so.

More: `docs/DEMO_SCRIPT.md`, `docs/SUBMISSION.md`, `docs/TEAM_GIT.md`, `HANDOFF.md`.

## Claim boundaries

- Makes unapproved or altered payments detectable; it does not prevent fraud and
  covers only payments routed through it.
- Regtest is a private chain controlled by whoever runs the node: the record is
  tamper-evident against edits to the off-chain log, not neutral or independently verifiable.
- Signing is local Ed25519 (not Sigstore/Cosign, no transparency log). Cryptographic
  behavior is internally tested; there is no third-party cryptographic verification,
  and the cryptography library used is not a FIPS 140-3 validated implementation.
- The Auditor is an agent inside the system, not a third-party verifier.
- Integrity proofs show origin, integrity and chronology of the record, not that an
  invoice or an agent's recommendation was correct.
- Approver names are self-typed; policy limits and vendor lists are demo values.

## Model choice and Endeavor

Set `AGENT_MODEL` to any model the endpoint serves. Flower's Endeavor 1.0 model
is supported by configuration only (set `AGENT_MODEL` to its model string); it is
not claimed in any demo until a live run on SuperGrid has confirmed it works with
this app's tool-calling loop.

## Team

Team Dusk, Flower Collaborative Agent Hackathon, Stanford, 29 Sep 2026: Louys
Henderson (SenseBeen Technologies), Flavia Sparacino, Sid Senthilkumar.

## License

Apache-2.0. See `LICENSE`.
