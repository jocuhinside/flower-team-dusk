# Submission checklist (Flower Collaborative Agent Hackathon, Stanford, 29 Sep 2026)

Deadline: submit before **demos begin at 17:15 PT**. A submission reminder goes out at 16:30. The build session ends 16:30.

Each team submits four things (per the event post):

- [ ] **Team details form** (team name, members, email addresses): https://flowerlabs.typeform.com/to/rQuplUGG. Owner: TBD.
- [ ] **Published Flower Hub app.** Publish the AgentApp following the event post's instructions. Owner: Flavia. Build locally first: `flwr build` succeeds on the current package (`hackathon.flower_app:app`, publisher `sensebeen` = Louys's Flower username; whoever publishes must be signed in as that account; license Apache-2.0 in `LICENSE`).
- [ ] **Short project description** (draft below). Owner: TBD.
- [ ] **GitHub repository link.** Candidates: `jocuhinside/flower-team-dusk` (private, empty; invites pending for `flaspa` and `sid-senthilkumar`) or `flaspa/flower-team-dusk` (Flavia's, empty at ~12:30 PT). Agree which one the submission links; a private repo may not be viewable by judges, so make it public or use Flavia's. Push with `scripts/git_setup.sh`. Owner: TBD.

## Before pushing the repo
- [ ] Confirm `.env`, `keys/`, `demo/`, `.venv`, `*.fab` are ignored (they are in `.gitignore`).
- [x] `Claude outputs/`, `ansible/inventory.ini` and `rpc-credentials.env` are now in `.gitignore` (inventory holds a real host address and username).
- [x] Public-upload review: internal hostnames/usernames removed from `ansible/preflight.yml` and docs; `LICENSE` (Apache-2.0) and `README.md` present.
- [ ] Run `git status` and check no key, token or API value is staged. Never paste keys into chat or commits.
- [ ] `uv sync` so `uv.lock` includes `cryptography`.
- [ ] `uv run ruff check . && uv run pytest -q`.
- [ ] Add a remote and push (repo owner does this; no remote is configured yet).
- [x] README: pitch, run steps, claim boundaries (done 29 Sep).

## Model access on the day
- Nebius API key is shared in the Slack channel during the event. The Flower runtime reads `FLWR_MODEL_API_KEY` and `FLWR_MODEL_API_ENDPOINT`; our app accepts those or the `SPONSOR_*` names. On SuperGrid the runtime injects `FLWR_RUNTIME_BASE_URL` and `FLWR_RUNTIME_API_KEY`, so only the model name is needed: set `AGENT_MODEL` (or `SPONSOR_MODEL`). For Endeavor, ask Flower staff for the exact model string first.
- **Ask a mentor:** does the endpoint support the Responses API tool-calling loop this app uses? If not, that one call needs changing.
- Ask for SuperGrid allow-listing using your registered Flower account username.

## Draft short description (edit freely)
> **HOA Payment Accountability Chain.** Flower agents on SuperGrid review a vendor payment: one extracts the invoice fields, others check budget, reserve and vendor rules, and an arbiter recommends PAY or HOLD. Two board members approve the exact proposal hash. Every handoff is recorded as a hash-chained event, the evidence bundle is signed, and event hashes are anchored to a Bitcoin regtest node. An Auditor agent refuses to finalize a run whose record does not verify. Altering the payee, deleting an event, or even rewriting all hashes is detected by one verifier command. Makes altered payments detectable; it does not prevent fraud, and regtest anchoring is a private-chain demonstration.

## Judging (from the event post)
Use of Flower (listed first), Impact and Originality, Demo and Delivery. Demo is 3-5 minutes: how Flower and SuperGrid enable it, the problem, the working result. Use of Flower is the biggest risk: get an end-to-end SuperGrid run working early and keep a recorded or offline fallback.

## Status snapshot (see `claude/build-status-flower-hoa.md` in the Project)
Built and unit-tested offline. **Not yet confirmed:** run on SuperGrid, Hub publish, live Bitcoin regtest node, push to GitHub.
