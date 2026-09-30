#!/usr/bin/env bash
# Offline demo rehearsal: runs the test suite, rebuilds the demo, and runs every demo step
# with its expected result. Exits non-zero if any step does not behave as the script says.
# No network, model or keys needed (the simulated ledger is used; say so in the demo).
#
# Usage (from the repo root):  bash scripts/offline_demo.sh [--skip-tests]
set -uo pipefail
cd "$(dirname "$0")/.."

mkdir -p accountability-runs  # already in .gitignore
LOG="accountability-runs/demo-rehearsal-$(date +%Y%m%d-%H%M%S).md"
PUB="demo/keys/demo-signer.pub"
failures=0

run() {  # run <expected PASS|FAIL> <command...>
  local expected="$1"; shift
  echo; echo "\$ uv run hackathon $*"
  uv run --quiet hackathon "$@"
  local code=$?
  local got=PASS; [ "$code" -ne 0 ] && got=FAIL
  if [ "$got" = "$expected" ]; then
    echo ">> OK: expected $expected, got $got"
  else
    echo ">> MISMATCH: expected $expected, got $got"; failures=$((failures + 1))
  fi
}

{
  echo "Offline demo rehearsal, $(date)"
  if [ "${1:-}" != "--skip-tests" ]; then
    echo; echo "\$ uv run pytest -q"
    uv run --quiet pytest -q || failures=$((failures + 1))
  fi

  echo; echo "\$ uv run hackathon reset && uv run hackathon demo && uv run hackathon chain-demo"
  uv run --quiet hackathon reset >/dev/null
  uv run --quiet hackathon demo || failures=$((failures + 1))
  uv run --quiet hackathon chain-demo || failures=$((failures + 1))

  run PASS verify demo/golden --pubkey "$PUB"              # honest record
  run FAIL verify demo/tampered --pubkey "$PUB"            # payee changed
  run FAIL verify demo/tampered-rehashed --pubkey "$PUB"   # payee changed, all hashes rewritten
  run PASS audit-chain demo/chain/golden                   # agent trace intact
  run FAIL audit-chain demo/chain/tampered
  run FAIL audit-chain demo/chain/tampered-rehashed
  run FAIL audit-chain demo/chain/deleted-event            # one event deleted

  echo
  echo "Not covered here: 'uv run hackathon approve-ui --approvers 2' (interactive page)."
  if [ "$failures" -eq 0 ]; then echo "REHEARSAL: ALL STEPS AS EXPECTED"; else echo "REHEARSAL: $failures PROBLEM(S)"; fi
} 2>&1 | tee "$LOG"

echo "Log saved to $LOG"
grep -q "REHEARSAL: ALL STEPS AS EXPECTED" "$LOG"
