# Demo script (target 4:30, limit 5:00)

Story: **HOA payment accountability chain.** One volunteer treasurer often controls the bank account. Our Flower agents review each vendor payment, two board members approve the exact proposal hash, and every step is hashed, signed and anchored. An altered payment is detectable afterward.

Tagline: **"The AI can change its mind; the evidence cannot change invisibly."**

## Before you go on stage (do this once, at the desk)
1. `uv sync --python 3.12` then `uv run pytest -q` (all green).
2. `uv run hackathon keygen` (only if `keys/` is empty; never commit `keys/`).
3. `uv run hackathon demo` and `uv run hackathon chain-demo` (builds everything offline).
4. Open three terminal tabs: **A** verify commands, **B** approval screen, **C** SuperGrid/Flower run.
5. Regtest, if the regtest host is up: `source ~/regtest-anchor/rpc-credentials.env`, then `uv run hackathon chain-demo --ledger bitcoin`. **If not confirmed working, use the simulated ledger and say so.**
6. Between rehearsals: `uv run hackathon reset`, then rebuild with step 3.

## Timed script

| Time | Show | Say |
|---|---|---|
| 0:00-0:30 | Slide or terminal title | "HOAs run on volunteer boards, and one treasurer often controls the bank account. Altered payments usually surface after reserves are gone. We add a tamper-evident record to every payment." |
| 0:30-1:15 | Tab C: Flower orchestrator delegating over SuperGrid (**Flavia to confirm the exact run command and what appears on screen**) | "A Flower orchestrator sends a $4,800 pool-repair invoice to specialist agents on SuperGrid: one reads the invoice, one checks the vendor list. Each handoff becomes a hashed event." |
| 1:15-2:00 | Tab B: `uv run hackathon approve-ui --approvers 2` (open the local page) | "The Arbiter recommends PAY. Two board members type the exact hash they reviewed. One approval is not enough, and a wrong hash is rejected." Approve as two different names. |
| 2:00-2:45 | Tab A: `uv run hackathon verify demo/golden --pubkey demo/keys/demo-signer.pub` | "A separate verifier recomputes every hash and checks the signature. Green means the record is exactly what the board approved." |
| 2:45-3:45 | Tab A: verify `demo/tampered`, then `demo/tampered-rehashed` (same command, same key) | "Now someone changes the payee to an unknown LLC. FAIL. A smarter attacker rewrites every hash too. The signature still fails. Same command, one changed field." |
| 3:45-4:15 | Tab A: `uv run hackathon audit-chain demo/chain/deleted-event` (add `--ledger bitcoin` only if the live node is up) | "Delete an event from the log and the trace commitment and anchors no longer match. The Auditor will not let the run finalize." |
| 4:15-4:30 | Closing line | "Flower agents collaborate; the evidence layer makes the record tamper-evident. The same stack applies to vendor payments, migration evidence and sensor records." |

## If something breaks
- **SuperGrid or the model endpoint fails:** skip tab C, say "recorded run" only if you actually have one; otherwise run `hackathon chain-demo` and describe the Flower step honestly.
- **Regtest not up:** use the simulated ledger and say "simulated ledger; Bitcoin regtest is the production-style path".
- **Approval page fails:** use `demo/golden`, which already contains two demo approvals.
- **Verifier prints something unexpected:** re-run `hackathon reset`, then the demo build. Do not improvise around it.

## Say this (claim boundaries)
- "Makes unapproved or altered payments **detectable**." It does not prevent embezzlement, and it covers only payments routed through it.
- "Tamper-evident against edits to the log." Regtest is a private chain controlled by whoever runs the node; production would anchor to a public chain run by someone other than the treasurer.
- "Signing is local Ed25519, internally tested." No third-party cryptographic verification; the crypto library is not FIPS 140-3 validated; not Sigstore.
- "The Auditor is an agent inside the system, not a third-party verifier."
- "It shows origin, integrity and chronology, not that the invoice or the agents' recommendation was correct."
- "Approver names are typed in for the demo; vendor lists and limits are demo values."

## Do not say
"first", "only", "certified", "compliant", "independently verified", "neutral ledger", "prevents fraud", "trustless".

## Q&A prep
- **What does Flower do here?** Runs the orchestrator and specialists across SuperGrid; the accountability layer wraps their Grid calls.
- **Why Bitcoin?** Only a 37-byte commitment goes on-chain, so the record has an external timestamp without putting invoices or prompts anywhere public. Regtest for the demo.
- **What stops the orchestrator lying about a specialist?** Today, specialists send hash-only attestations and the orchestrator records them; specialists do not sign their own events yet. Per-agent keys are the next step.
- **Who approved?** Names are self-typed for the demo; real use needs authenticated board identities.
