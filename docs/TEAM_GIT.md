# Team git guide

Two remotes, one codebase:

- `origin`: `https://github.com/jocuhinside/flower-team-dusk` (private; owner: Louys; invites sent to `flaspa` and `sid-senthilkumar`, both must accept before they can push).
- `team`: the shared repo the hackathon submission links to, `https://github.com/flaspa/flower-team-dusk` (Flavia, GitHub `flaspa`). It was empty at ~12:30 PT.

## One-time setup (repo owner, on the machine holding the working copy)
```
cd ~/hackathon
./scripts/git_setup.sh git@github.com:<YOU>/<REPO>.git https://github.com/flaspa/flower-team-dusk.git
```
The script adds the remotes (never overwriting an existing one), makes sure `.env`, `keys/`, `demo/`, `*.fab` and similar are ignored, scans for secrets (prints file:line only), shows `git status`, asks to confirm, commits, and pushes. If the scan finds anything it stops before committing.

Use a different name than `team` if you prefer: `git remote rename team <name>`.

## Give Flavia access
Pick one:
1. **Collaborator:** on your repo, Settings > Collaborators > Add people > `flaspa`. She clones your repo and pushes branches.
2. **Team repo:** you push to `flaspa/flower-team-dusk` (needs her to add you as a collaborator there). Either way, the submission links one repo, so agree which.

## Daily flow
```
git pull --rebase origin main          # before you start
git switch -c <name>/<topic>           # e.g. flavia/agentapp, louys/regtest
# work, then
uv run ruff check . && uv run pytest -q
git add -p && git commit -m "..."
git push -u origin <name>/<topic>      # open a PR into main
```
Suggested ownership to avoid conflicts: Flavia owns `src/hackathon/flower_app.py` and `flower_agents.py`; the trust layer (`schemas.py`, `bundle.py`, `signing.py`, `events.py`, `auditor.py`, `ledger.py`, `ui.py`) and `ansible/`, `scripts/` are Louys's. Tell each other before editing across those lines. `schemas.py` and `roles.py` are shared contracts; change them by PR only.

## Never commit
`.env`, anything in `keys/`, `rpc-credentials.env`, API keys, `*.fab` build files. If a secret is ever pushed, rotate it immediately; deleting the commit is not enough.

## CI
The remote tools could not write into `.github/`, so the workflow is at `docs/ci.yml.example`. Enable it once:
```
mkdir -p .github/workflows && mv docs/ci.yml.example .github/workflows/ci.yml
```
It runs `ruff` and `pytest` on every push and PR. It has not run on GitHub yet; check the Actions tab after the first push.
