#!/usr/bin/env bash
# Start a private Bitcoin Core REGTEST node for the accountability demo.
# Uses its own data directory and RPC port; it never touches a mainnet node or its data.
set -euo pipefail

DATADIR="${REGTEST_DATADIR:-$HOME/regtest-anchor}"
RPCPORT="${REGTEST_RPCPORT:-18443}"
CREDS="$DATADIR/rpc-credentials.env"

command -v bitcoind >/dev/null || { echo "bitcoind not found in PATH" >&2; exit 1; }
command -v bitcoin-cli >/dev/null || { echo "bitcoin-cli not found in PATH" >&2; exit 1; }

mkdir -p "$DATADIR"
chmod 700 "$DATADIR"
if [ ! -f "$CREDS" ]; then
  umask 077
  {
    echo "export BITCOIN_RPC_USER=regtest"
    echo "export BITCOIN_RPC_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
    echo "export BITCOIN_RPC_URL=http://127.0.0.1:$RPCPORT"
  } > "$CREDS"
fi
# shellcheck disable=SC1090
source "$CREDS"

if bitcoin-cli -regtest -datadir="$DATADIR" -rpcport="$RPCPORT" \
     -rpcuser="$BITCOIN_RPC_USER" -rpcpassword="$BITCOIN_RPC_PASSWORD" getblockchaininfo >/dev/null 2>&1; then
  echo "regtest node already running on port $RPCPORT"
else
  bitcoind -regtest -daemon -datadir="$DATADIR" -txindex=1 -fallbackfee=0.0001 \
    -server=1 -listen=0 -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport="$RPCPORT" \
    -rpcuser="$BITCOIN_RPC_USER" -rpcpassword="$BITCOIN_RPC_PASSWORD"
  for _ in $(seq 1 30); do
    bitcoin-cli -regtest -datadir="$DATADIR" -rpcport="$RPCPORT" \
      -rpcuser="$BITCOIN_RPC_USER" -rpcpassword="$BITCOIN_RPC_PASSWORD" getblockchaininfo >/dev/null 2>&1 && break
    sleep 1
  done
fi

echo "Regtest node ready. In your shell run:"
echo "  source $CREDS"
echo "  uv run hackathon chain-demo --ledger bitcoin"
