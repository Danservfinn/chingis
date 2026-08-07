#!/usr/bin/env bash
# L0 — git guard. Plan §7.
#
# Rejects staged changes under any protected path unless CHINGIS_OPERATOR=1 is set
# in the environment, and enforces reflex provenance on the routing policy.
#
# Installed as .git/hooks/pre-commit by `ops/permissions.sh install`.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

PROTECTED_LIST="ops/protected_paths.txt"
fail=0

# --- Guard 1: protected paths ------------------------------------------------
if [[ -f "$PROTECTED_LIST" ]]; then
  # NOTE: macOS ships bash 3.2. No mapfile, no associative arrays, no ${x^^}.
  # This hook is the write-set's first layer -- it must run on the stock shell.
  protected=()
  while IFS= read -r p; do
    [[ -n "$p" ]] && protected+=("$p")
  done < <(grep -vE '^[[:space:]]*(#|$)' "$PROTECTED_LIST")

  staged="$(git diff --cached --name-only --diff-filter=ACMRD || true)"

  hits=()
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    for p in "${protected[@]}"; do
      if [[ "$file" == "$p"* ]]; then
        hits+=("$file  (protected by: $p)")
        break
      fi
    done
  done <<< "$staged"

  if (( ${#hits[@]} > 0 )); then
    if [[ "${CHINGIS_OPERATOR:-}" == "1" ]]; then
      echo "CHINGIS_OPERATOR=1 — permitting operator write to ${#hits[@]} protected path(s):"
      printf '  %s\n' "${hits[@]}"
      echo "This commit is the audit trail for the audit trail. Make the message say why."
    else
      echo "BLOCKED: ${#hits[@]} staged change(s) under the protected write-set:" >&2
      printf '  %s\n' "${hits[@]}" >&2
      cat >&2 <<'EOF'

kernel/, verify/seeded/, evals/, docs/, ops/, migrations/, and the frozen schemas
sit outside the loop's write access. The gate never learns.

If you are the operator and this change is deliberate and off-cycle:

    CHINGIS_OPERATOR=1 git commit -m "operator: <why this gate change is correct>"

Do not export CHINGIS_OPERATOR persistently. Inline, once, per commit.
EOF
      fail=1
    fi
  fi
fi

# --- Guard 2: reflex provenance ----------------------------------------------
if git diff --cached --name-only | grep -q '^policy/routing_policy.yaml$'; then
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  git show ":policy/routing_policy.yaml" > "$tmp"
  if ! python3 ops/check_policy_provenance.py "$tmp"; then
    echo "BLOCKED: routing policy failed the provenance check." >&2
    echo "Every reflex is executive-authored, decision-traceable, and TTL'd. No exceptions, operator included." >&2
    fail=1   # deliberately NOT bypassable by CHINGIS_OPERATOR
  fi
fi

exit "$fail"
