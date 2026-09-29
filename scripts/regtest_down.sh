#!/usr/bin/env bash
# Stop the private regtest node started by regtest_up.sh. Does not delete any data.
set -euo pipefail
DATADIR="${REGTEST_DATADIR:-$HOME/regtest-anchor}"
RPCPORT="${REGTEST_RPCPORT:-18443}"
# shellcheck disable=SC1091
source "$DATADIR/rpc-credentials.env"
bitcoin-cli -regtest -datadir="$DATADIR" -rpcport="$RPCPORT" \
  -rpcuser="$BITCOIN_RPC_USER" -rpcpassword="$BITCOIN_RPC_PASSWORD" stop
