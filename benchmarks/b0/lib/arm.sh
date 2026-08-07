#!/usr/bin/env bash
# Fleet invocation for B0. Sourced by run_b0.sh.
#
# Two fleets, two operations. Everything else in B0 is bookkeeping.
#
# !! FLAG DRIFT !!  codex and claude flags drift between releases. These invocations
# match the spec §4 / plan §6 forms as written on 2026-08-06. `preflight.sh` diffs them
# against the installed `--help` and refuses to run on a mismatch. Pin both in
# .tool-versions and do not chase upgrades until B3.

set -euo pipefail

B0_WALL_S="${B0_WALL_S:-900}"

# ---------------------------------------------------------------- extraction --
# Both fleets wrap the model's text in JSON. Pull the text out, tolerantly:
# a fleet that changes its envelope should degrade to "raw output", not to silence.
# Claude Code may print warnings to stdout ahead of the JSON body ("claude.ai connectors
# are disabled because ANTHROPIC_API_KEY ... is set"). jq on the raw file then fails and the
# arm looks empty. Carve out the JSON object first.
_json_body() {
  python3 -c "
import sys, json
raw = open(sys.argv[1], encoding='utf-8', errors='replace').read()
i = raw.find('{')
print(raw[i:] if i >= 0 else raw, end='')
" "$1"
}

_extract_text() {
  local raw_file="$1"
  _json_body "$raw_file" > "$raw_file.body"
  if jq -e -r '.result' "$raw_file.body" 2>/dev/null; then return 0; fi
  if jq -e -r '.result' "$raw_file" 2>/dev/null; then return 0; fi
  # codex exec --json emits JSONL; take the last event carrying assistant text.
  if jq -s -e -r '[.[] | (.message.content? // .text? // empty)] | last' "$raw_file" 2>/dev/null; then return 0; fi
  cat "$raw_file"
}

# Reviewers are told to emit exactly one fenced JSON block. Take the last one:
# a model that reasons in prose before answering puts its answer last.
_extract_json_block() {
  python3 - "$1" <<'PY'
import json, re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S)
for block in reversed(blocks):
    try:
        json.dumps(json.loads(block))
    except Exception:
        continue
    print(block.strip()); sys.exit(0)
# No fenced block. Try the whole thing before giving up.
try:
    json.loads(text); print(text.strip()); sys.exit(0)
except Exception:
    pass
print(json.dumps({"findings": [], "verdict": "unparseable", "_parse_error": True}))
PY
}

# ---------------------------------------------------------------- generation --
# $1 fleet (W1|W2)  $2 worktree  $3 prompt file  $4 raw output path
fleet_generate() {
  local fleet="$1" worktree="$2" prompt="$3" out="$4"

  if [[ "${B0_DRY_RUN:-0}" == "1" ]]; then
    printf '{"result":"[dry-run] %s would have generated a diff in %s"}\n' "$fleet" "$worktree" > "$out"
    printf '# dry-run stub change by %s\n' "$fleet" >> "$worktree/.b0_dryrun_marker"
    return 0
  fi

  case "$fleet" in
    W1)
      # Anthropic natively. env -u strips any redirect that would silently turn W1 into a
      # second W2 -- same lab twice, reported as cross-fleet. Credentials come from the
      # Keychain via Claude Code's own OAuth.
      # Prompt on STDIN, never as a positional. `--add-dir <directories...>` is variadic
      # and silently swallows a trailing prompt argument, which fails as "Input must be
      # provided" -- a flag-arity bug that looks exactly like a fleet outage.
      # cd into the worktree. --add-dir GRANTS access; cwd decides where the worker
      # actually writes. Without it the worker writes into the harness repo, every diff
      # comes back empty, and on the next run it finds its own earlier output and reports
      # "already exists" -- three symptoms, one missing chdir.
      ( cd "$worktree" && \
        env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
          gtimeout "${B0_WALL_S}s" claude -p --output-format json \
          --permission-mode acceptEdits --allowedTools Edit Write Bash Read Glob Grep \
          --model "${B0_W1_MODEL:-sonnet}" --add-dir "$worktree" \
          < "$prompt" ) > "$out" 2>"$out.stderr"
      ;;
    W2)
      ( cd "$worktree" && \
        ANTHROPIC_BASE_URL="${ZAI_ANTHROPIC_ENDPOINT:-https://api.z.ai/api/anthropic}" \
        ANTHROPIC_API_KEY="${ZAI_KEY:-$ZAI_API_KEY}" \
        gtimeout "${B0_WALL_S}s" claude -p --output-format json \
          --permission-mode acceptEdits --allowedTools Edit Write Bash Read Glob Grep \
          --model "${B0_W2_MODEL:-glm-4.6}" --add-dir "$worktree" \
          < "$prompt" ) > "$out" 2>"$out.stderr"
      ;;
    *) echo "unknown fleet: $fleet" >&2; return 2 ;;
  esac
}

# ------------------------------------------------------------------- review --
# $1 fleet  $2 prompt file  $3 findings json out
# The reviewer gets the rubric, the objective, the diff, and the V1 report. It does NOT
# get: whether the contract is seeded, which class was seeded, or which fleet generated.
# Blinding is the whole experiment.
fleet_review() {
  local fleet="$1" prompt="$2" out="$3"
  local raw="${out%.json}.raw"

  if [[ "${B0_DRY_RUN:-0}" == "1" ]]; then
    cat > "$out" <<'EOF'
{"findings":[{"file":"dry/run.py","line":1,"class":"other","severity":"low","description":"dry-run stub finding"}],"verdict":"reject"}
EOF
    return 0
  fi

  case "$fleet" in
    W1)
      env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
        gtimeout "${B0_WALL_S}s" claude -p --output-format json \
        --model "${B0_W1_MODEL:-sonnet}" < "$prompt" > "$raw" 2>"$raw.stderr" || true
      ;;
    W2)
      ANTHROPIC_BASE_URL="${ZAI_ANTHROPIC_ENDPOINT:-https://api.z.ai/api/anthropic}" \
      ANTHROPIC_API_KEY="${ZAI_KEY:-$ZAI_API_KEY}" \
      gtimeout "${B0_WALL_S}s" claude -p --output-format json \
        < "$prompt" > "$raw" 2>"$raw.stderr" || true
      ;;
    *) echo "unknown fleet: $fleet" >&2; return 2 ;;
  esac

  _extract_text "$raw" > "$raw.text"
  _extract_json_block "$raw.text" > "$out"
}
