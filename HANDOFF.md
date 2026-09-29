# Hackathon Handoff: Trust + Evidence Layer

Event: Collaborative Agent Hackathon @ Stanford (Flower Labs). One day, teams of 2-4. Build window 11:00-5:15, demos 5:30-6:45.
Theme: safe, human-supervised multi-agent collaboration on Flower.

## The one-line pitch
"The AI can change its mind; the evidence cannot change invisibly."
Given an AI-generated proposal, we show the decision path, bind the human approval to the proposal hash, produce a signed evidence bundle, and show that even a tiny alteration is detected.

## Who owns what
- **Agent repo (Flavia, GitHub):** the Flower agent chain and role definitions. Replace the bodies in `roles.py` with Flower agent calls; keep the typed outputs (`Proposal`, `AgentVerdict`).
- **Trust layer (this repo):** schemas, canonical hashing, human gate, bundles, verifier, signing, approval screen, tamper demo.
- **Agree early:** schema names. The plan says `Proposal` / `AgentVerdict`; older code has `AgentFinding` (collector / policy / red-team). Both exist now; pick one path before the build window.

> Update 29 Sep: this file is the original trust-layer handoff. The Flower app, HOA scenario,
> two-approver flow, accountability events, Auditor and ledger adapters were added afterwards;
> see `README.md` and `docs/SUBMISSION.md` for current status.

## What is built (31 tests at handoff time; the suite has grown since)
- Strict Pydantic contracts (unknown fields rejected, no type coercion, frozen objects).
- Canonical JSON + SHA-256.
- Analyst -> Skeptic -> Safety -> Arbiter chain (rule-based stand-ins). Every verdict carries the hash of the exact proposal it reviewed. Verifier fails on wrong role order, unbound verdicts, or an Arbiter approving over a block.
- Human approval bound to the exact review hash; wrong hash or empty name fails closed.
- Evidence bundle on disk: `manifest.json`, `manifest.sha256`, `manifest.dsse.json`.
- Verifier with per-check PASS/FAIL and reason codes; exit code 0 = pass, 1 = fail.
- Local Ed25519 signing (DSSE-style). Verifier needs the trusted public key via `--pubkey`.
- Local approval screen on 127.0.0.1.
- Demo with three bundles: `golden` (PASS), `tampered` (FAIL), `tampered-rehashed` (only the signature check catches it).

## Commands
```
uv sync --python 3.12
uv run hackathon keygen                      # once; keys/ is gitignored, never commit the .key
uv run hackathon demo                        # builds demo/golden, demo/tampered, demo/tampered-rehashed
uv run hackathon verify demo/golden --pubkey demo/keys/demo-signer.pub
uv run hackathon verify demo/tampered --pubkey demo/keys/demo-signer.pub
uv run hackathon verify demo/tampered-rehashed --pubkey demo/keys/demo-signer.pub
uv run hackathon approve-ui                  # needs keygen first
uv run hackathon reset                       # clears demo/
uv run ruff check . && uv run pytest
```
Sponsor API settings go in `.env` (copy `.env.example`). Never commit `.env` or paste keys into chat.

## Not done yet
- Flower app is written but has not been run end to end on SuperGrid.
- Bitcoin anchoring has been tested against a fake RPC only, not a live regtest node.
- Merkle root, Neo4j lineage, in-toto/SLSA-style provenance, SBOM (P1/P2; keep them off the critical path).
- Sigstore/Cosign signing (current signing is a local Ed25519 stand-in).

## Claim boundaries (keep these in slides and the pitch)
- Signing is local Ed25519, not Sigstore/Cosign, with no transparency log. It is internally tested; no third party has validated it, and the crypto library is not FIPS-validated.
- The signature shows origin, integrity and chronology of the record. It does not show the agents' proposals were correct.
- The approver name is self-typed; the screen does not authenticate the person.
- Without a trusted public key, someone who edits the manifest and rewrites its digest passes the digest check. The signature check is what catches that.
- Use "verifiable evidence stack" style wording; avoid "first", "only", or "certified".

## Demo tips
- Best moment: run the identical verify command on `golden` and `tampered-rehashed`. Same digests pass; the signature check fails.
- Policy limits in `roles.py` (amount limit, denylist) are demo values, not real controls.
- Run `hackathon reset` then `hackathon demo` before each rehearsal.
