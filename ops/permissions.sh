#!/usr/bin/env bash
# Write-set enforcement, layered weakest to strongest. Plan §7.
#
#   L0 — git guard:  pre-commit rejects protected-path changes without CHINGIS_OPERATOR=1
#   L1 — POSIX:      consolidation runs as user `khan`, read-only on protected dirs
#   L2 — macOS:      chflags uchg on kernel/*.py during cycle windows
#
# Run L0 minimum before the first cycle.
#
#   ./ops/permissions.sh install | status | l1 | lock | unlock

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

PROTECTED_LIST="ops/protected_paths.txt"
LOOP_USER="${CHINGIS_LOOP_USER:-khan}"

protected_paths() { grep -vE '^\s*(#|$)' "$PROTECTED_LIST"; }

# --- L0 ----------------------------------------------------------------------
cmd_install() {
  local hook=".git/hooks/pre-commit"
  mkdir -p .git/hooks
  cat > "$hook" <<'EOF'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/ops/precommit.sh"
EOF
  chmod +x "$hook" ops/precommit.sh ops/permissions.sh ops/check_policy_provenance.py
  echo "L0 installed: $hook -> ops/precommit.sh"
  echo "Protected write-set:"
  protected_paths | sed 's/^/  /'
}

# --- L1 ----------------------------------------------------------------------
cmd_l1() {
  if ! id -u "$LOOP_USER" >/dev/null 2>&1; then
    cat <<EOF
L1 requires a separate POSIX user for training/consolidation runs.

User '$LOOP_USER' does not exist. Create it (admin, interactive — not automated here
because it is a system-level account change you should make deliberately):

  sudo dscl . -create /Users/$LOOP_USER UserShell /bin/zsh
  sudo dscl . -create /Users/$LOOP_USER UniqueID 550
  sudo dscl . -create /Users/$LOOP_USER PrimaryGroupID 20
  sudo dscl . -create /Users/$LOOP_USER NFSHomeDirectory /Users/$LOOP_USER

Then re-run: ./ops/permissions.sh l1
EOF
    return 1
  fi
  while IFS= read -r p; do
    [[ -e "$p" ]] || continue
    chmod -R o-w "$p"
    echo "  o-w  $p"
  done < <(protected_paths)
  echo "L1 applied: '$LOOP_USER' (as other) has no write bit on the protected set."
  echo "Verify '$LOOP_USER' is NOT in this repo's group:  ls -ld ."
}

# --- L2 ----------------------------------------------------------------------
cmd_lock() {
  local n=0
  while IFS= read -r p; do
    [[ -e "$p" ]] || continue
    if [[ -d "$p" ]]; then
      while IFS= read -r f; do chflags uchg "$f" && n=$((n + 1)) || true; done \
        < <(find "$p" -type f -not -path '*/.git/*')
    else
      chflags uchg "$p" && n=$((n + 1))
    fi
  done < <(protected_paths)
  echo "L2 locked: chflags uchg on $n file(s). Cycle window is safe."
  echo "Operator lifts off-cycle with: ./ops/permissions.sh unlock"
}

cmd_unlock() {
  local n=0
  while IFS= read -r p; do
    [[ -e "$p" ]] || continue
    if [[ -d "$p" ]]; then
      while IFS= read -r f; do chflags nouchg "$f" && n=$((n + 1)) || true; done \
        < <(find "$p" -type f -not -path '*/.git/*')
    else
      chflags nouchg "$p" && n=$((n + 1))
    fi
  done < <(protected_paths)
  echo "L2 unlocked: $n file(s). You are off-cycle. Commit gate changes with CHINGIS_OPERATOR=1."
}

# --- status ------------------------------------------------------------------
cmd_status() {
  echo "== Write-set status =="
  if [[ -x .git/hooks/pre-commit ]] && grep -q precommit.sh .git/hooks/pre-commit 2>/dev/null; then
    echo "L0 git guard      : INSTALLED"
  else
    echo "L0 git guard      : NOT INSTALLED   <- run './ops/permissions.sh install'"
  fi

  if id -u "$LOOP_USER" >/dev/null 2>&1; then
    echo "L1 POSIX ($LOOP_USER)   : user exists"
  else
    echo "L1 POSIX ($LOOP_USER)   : user absent (not required until the first cycle, post-B6)"
  fi

  local locked=0 total=0
  while IFS= read -r p; do
    [[ -e "$p" ]] || continue
    while IFS= read -r f; do
      total=$((total + 1))
      ls -lO "$f" 2>/dev/null | grep -q uchg && locked=$((locked + 1)) || true
    done < <([[ -d "$p" ]] && find "$p" -type f -not -path '*/.git/*' || echo "$p")
  done < <(protected_paths)
  echo "L2 macOS flags    : $locked/$total protected files locked (uchg)"

  echo
  echo "CHINGIS_OPERATOR  : ${CHINGIS_OPERATOR:-unset}"
  [[ "${CHINGIS_OPERATOR:-}" == "1" ]] && \
    echo "  WARNING: exported persistently. The gate is open. Unset it." >&2
  echo
  echo "Protected paths:"
  protected_paths | sed 's/^/  /'
}

case "${1:-status}" in
  install) cmd_install ;;
  l1)      cmd_l1 ;;
  lock)    cmd_lock ;;
  unlock)  cmd_unlock ;;
  status)  cmd_status ;;
  *) echo "usage: $0 {install|status|l1|lock|unlock}" >&2; exit 2 ;;
esac
