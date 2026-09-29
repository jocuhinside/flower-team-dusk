#!/usr/bin/env bash
# Point this repo at your GitHub repo (and optionally the team repo), scan for secrets, commit, push.
#
#   ./scripts/git_setup.sh <your-repo-url> [team-repo-url] [--yes]
#   e.g. ./scripts/git_setup.sh git@github.com:YOU/flower-team-dusk.git https://github.com/flaspa/flower-team-dusk.git
#
# Never overwrites an existing remote with a different URL. Never prints secret values.
set -euo pipefail

YES=0; ARGS=()
for a in "$@"; do if [ "$a" = "--yes" ]; then YES=1; else ARGS+=("$a"); fi; done
ORIGIN_URL="${ARGS[0]:-}"; TEAM_URL="${ARGS[1]:-}"
[ -n "$ORIGIN_URL" ] || { sed -n '2,7p' "$0"; exit 2; }

cd "$(git rev-parse --show-toplevel)"

ensure_ignored() { grep -qxF "$1" .gitignore 2>/dev/null || echo "$1" >> .gitignore; }
for p in ".env" "keys/" "demo/" "*.fab" "Claude outputs/" "accountability-runs/" \
         "rpc-credentials.env" ".venv" ".ruff_cache/" ".pytest_cache/" "__pycache__/"; do
  ensure_ignored "$p"
done

add_remote() {
  local name="$1" url="$2" existing
  if existing=$(git remote get-url "$name" 2>/dev/null); then
    if [ "$existing" != "$url" ]; then
      echo "Remote '$name' already points at a different URL; not changing it." >&2
      echo "  existing: $existing" >&2; echo "  wanted:   $url" >&2; return 1
    fi
  else
    git remote add "$name" "$url"; echo "Added remote '$name'."
  fi
}
add_remote origin "$ORIGIN_URL"
[ -z "$TEAM_URL" ] || add_remote team "$TEAM_URL"

echo "== Secret scan (file:line only, values never printed)"
FILES=$(git ls-files -co --exclude-standard)
BAD=0
if echo "$FILES" | grep -Eq '(^|/)(\.env|rpc-credentials\.env)$|\.key$|\.pem$'; then
  echo "Refusing: a secret-looking file is not ignored:"; echo "$FILES" | grep -E '(^|/)(\.env|rpc-credentials\.env)$|\.key$|\.pem$'; BAD=1
fi
HITS=$(echo "$FILES" | xargs -d '\n' grep -InE \
  -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  -e "(API_KEY|SECRET|PASSWORD|TOKEN)[A-Z_]*[[:space:]]*[=:][[:space:]]*['\"]?[A-Za-z0-9_/+=-]{20,}" \
  2>/dev/null | grep -v 'os.environ' | grep -v '\.example' | cut -d: -f1,2 || true)
if [ -n "$HITS" ]; then echo "Possible secrets at:"; echo "$HITS"; BAD=1; fi
[ "$BAD" -eq 0 ] || { echo "Fix the above, then re-run. Nothing was committed or pushed." >&2; exit 1; }
echo "No secrets found."

git config user.email >/dev/null || { echo "Set git user.email and user.name first." >&2; exit 1; }
echo "== Changes to commit"; git status --short
if [ "$YES" -ne 1 ]; then read -r -p "Commit and push these changes? [y/N] " ans; [ "$ans" = "y" ] || exit 0; fi

git add -A
if git diff --cached --quiet; then echo "Nothing new to commit."; else
  git commit -m "Add accountability layer, Flower wiring, HOA demo and docs"
fi
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git push -u origin "$BRANCH"
[ -z "$TEAM_URL" ] || git push team "$BRANCH"
echo "Pushed '$BRANCH'. Invite Flavia (GitHub: flaspa) under Settings > Collaborators, or push to the team repo."
