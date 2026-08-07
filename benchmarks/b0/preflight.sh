#!/usr/bin/env bash
# What's installed, what's missing, and what each gap blocks.
# Run this before spending an evening discovering codex isn't authenticated.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
[[ -f .env ]] && set -a && source .env && set +a

ok=0; warn=0; bad=0
# NOTE: assign, never (( x++ )). Post-increment evaluates to the OLD value, so (( ok++ ))
# exits 1 when ok is 0 -- which makes `pass ... || fail ...` print BOTH, and under `set -e`
# aborts the script outright. Subtle, and wrong in two different ways.
pass() { printf '  \033[32mok\033[0m    %s\n' "$*"; ok=$((ok + 1)); }
soft() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; warn=$((warn + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; bad=$((bad + 1)); }

echo "== Tooling =="
command -v jq       >/dev/null && pass "jq"       || fail "jq — the runner parses every fleet response with it"
command -v gtimeout >/dev/null && pass "gtimeout" || fail "gtimeout — 'brew install coreutils'; no wall clock = a hung fleet eats the weekend"
command -v python3  >/dev/null && pass "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || fail "python3 — injector and scorer"
command -v git      >/dev/null && pass "git"      || fail "git"

echo
echo "== Fleets =="
if command -v codex >/dev/null; then
  pass "codex $(codex --version 2>&1 | head -1)"
  codex exec --help 2>&1 | grep -q -- '--sandbox' \
    && pass "codex exec --sandbox present" \
    || soft "codex exec --sandbox NOT in --help — flags drift between releases; verify lib/arm.sh against the installed CLI"
else
  fail "codex NOT INSTALLED — blocks BOTH W1 arms (gen-W1 and rev-W1), i.e. half of B0"
fi

if command -v claude >/dev/null; then
  pass "claude $(claude --version 2>&1 | head -1)"
else
  fail "claude NOT INSTALLED — blocks both W2 arms"
fi

echo
echo "== Credentials =="
[[ -n "${ZAI_CODING_ENDPOINT:-}" ]] && pass "ZAI_CODING_ENDPOINT set" || fail "ZAI_CODING_ENDPOINT unset in .env — W2 cannot reach the GLM plan"
[[ -n "${ZAI_KEY:-}" ]]            && pass "ZAI_KEY set"              || fail "ZAI_KEY unset in .env"
[[ -n "${OPENAI_API_KEY:-}" ]]     && pass "OPENAI_API_KEY set"       || soft "OPENAI_API_KEY unset — not needed for B0, needed at B3"

echo
echo "== B0 corpus =="
shopt -s nullglob
n=$(ls benchmarks/b0/contracts/c_*.json 2>/dev/null | wc -l | tr -d ' ')
[[ "$n" -eq 20 ]] && pass "$n/20 contracts authored" \
  || soft "$n/20 contracts authored — plan §5 calls for 20 (10 seeded, 10 clean)"

tbd=0
for f in benchmarks/b0/contracts/c_*.json; do
  grep -q '"TBD' "$f" && tbd=$((tbd + 1))
done
(( tbd == 0 )) && [[ "$n" -gt 0 ]] && pass "no TBD placeholders in contracts" \
  || soft "$tbd contract(s) still contain TBD placeholders — point them at real repos"

grep -q 'TBD' verify/seeded/manifest.json \
  && soft "verify/seeded/manifest.json still has TBD injection targets — injections will fail and pairs will be excluded" \
  || pass "seeded manifest has no TBD targets"

grep -q '☐ LOCKED' benchmarks/b0/PREREGISTRATION.md \
  && soft "PREREGISTRATION.md is NOT locked — lock and commit it before the first fleet call" \
  || pass "pre-registration locked"

echo
echo "== Write-set =="
if [[ -x .git/hooks/pre-commit ]] && grep -q precommit.sh .git/hooks/pre-commit 2>/dev/null; then
  pass "L0 git guard installed"
else
  fail "L0 git guard NOT installed — './ops/permissions.sh install'"
fi

echo
printf '%d ok, %d warn, %d fail\n' "$ok" "$warn" "$bad"
if (( bad > 0 )); then
  echo
  echo "Fix the failures, or run './benchmarks/b0/run_b0.sh --dry-run' to exercise the"
  echo "pipeline end-to-end without touching a fleet."
  exit 1
fi
