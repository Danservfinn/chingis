#!/usr/bin/env bash
# B0 — the experiment before the code. Plan §5 Weekend 1.
#
# Deliberately a dumb bash script. It is also the thing B6 formalises as the permanent
# M0 control arm — the fixed-routing, fixed-retry, no-executive shell that Chingis has to
# beat. Keep it dumb on purpose.
#
#   ./run_b0.sh [--dry-run] [--only c_0001] [--phase generate|review|all]
#
# Design note — 40 generations, not 80. Each contract is generated ONCE per fleet, and
# each artifact is then reviewed by BOTH fleets. The four arms fall out as:
#
#     gen W1 -> reviewed by W1 (self) and W2 (cross)
#     gen W2 -> reviewed by W2 (self) and W1 (cross)
#
# This is a paired design: self and cross review the *identical artifact*, so the
# contrast B0 is testing is not confounded by generation variance. It also halves spend.

set -euo pipefail

B0_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$B0_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=lib/arm.sh
source "$B0_DIR/lib/arm.sh"

CONTRACTS_DIR="$B0_DIR/contracts"
ARTIFACTS="$B0_DIR/artifacts"
RESULTS="$B0_DIR/results"
MANIFEST="verify/seeded/manifest.json"
RUBRIC="verify/rubric/review_rubric.md"
WORKTREES="$REPO_ROOT/.worktrees"

export B0_DRY_RUN=0
ONLY=""
PHASE="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) export B0_DRY_RUN=1; shift ;;
    --only)    ONLY="$2"; shift 2 ;;
    --phase)   PHASE="$2"; shift 2 ;;
    *) echo "usage: $0 [--dry-run] [--only c_NNNN] [--phase generate|review|all]" >&2; exit 2 ;;
  esac
done

[[ -f .env ]] && set -a && source .env && set +a || true
mkdir -p "$ARTIFACTS" "$RESULTS" "$WORKTREES"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

[[ "$B0_DRY_RUN" == "1" ]] && log "DRY RUN — no fleet is invoked, no quota is spent."

shopt -s nullglob
contract_files=("$CONTRACTS_DIR"/c_*.json)
(( ${#contract_files[@]} > 0 )) || die "no contracts in $CONTRACTS_DIR. Author them first (plan §5 step 1)."

# =============================================================== generation ===
phase_generate() {
  for cf in "${contract_files[@]}"; do
    cid="$(jq -r '.contract_id' "$cf")"
    [[ -n "$ONLY" && "$cid" != "$ONLY" ]] && continue

    repo="$(jq -r '.repo' "$cf")"
    base="$(jq -r '.base_ref // "HEAD"' "$cf")"
    objective="$(jq -r '.objective' "$cf")"
    v1cmd="$(jq -r '.v1_command // "true"' "$cf")"

    [[ "$repo" == "TBD"* || -z "$repo" || "$repo" == "null" ]] && \
      die "$cid: .repo is unset. Point contracts at real repositories before running."
    [[ -d "$repo/.git" ]] || die "$cid: $repo is not a git repository."

    for fleet in W1 W2; do
      outdir="$ARTIFACTS/$cid/$fleet"
      if [[ -s "$outdir/diff.patch" ]]; then
        log "$cid/$fleet generation: cached, skipping"
        continue
      fi
      mkdir -p "$outdir"

      wt="$WORKTREES/${cid}_${fleet}"
      rm -rf "$wt"
      git -C "$repo" worktree add --detach --quiet "$wt" "$base" 2>/dev/null \
        || die "$cid/$fleet: could not create worktree at $base"

      # The prompt is the contract objective. Nothing about seeding, nothing about
      # the other fleet, nothing about review.
      printf '%s\n\nWork only inside this checkout. Make the change and stop.\n' \
        "$objective" > "$outdir/contract_prompt.md"

      log "$cid/$fleet generating..."
      if ! fleet_generate "$fleet" "$wt" "$outdir/contract_prompt.md" "$outdir/raw.json"; then
        log "$cid/$fleet generation FAILED (see $outdir/raw.json.stderr)"
        echo "generation_failed" > "$outdir/STATUS"
        git -C "$repo" worktree remove --force "$wt" 2>/dev/null || true
        continue
      fi

      # --- seeded-defect injection, post-generation and deterministic -----------
      inj_status="none"
      if [[ "$(jq -r --arg c "$cid" '.contracts[] | select(.contract_id==$c) | .seeded' "$MANIFEST")" == "true" ]]; then
        # --out-dir is outside the worktree on purpose: ground truth written inside it
        # would land in the diff and un-blind every reviewer.
        if python3 "$B0_DIR/inject.py" --manifest "$MANIFEST" --contract "$cid" \
             --worktree "$wt" --out-dir "$outdir"; then
          inj_status="injected"
        else
          inj_status="injection_failed"
          log "$cid/$fleet INJECTION FAILED — pair excluded from the catch-rate denominator"
        fi
      fi
      echo "$inj_status" > "$outdir/INJECTION"

      # The artifact is the diff. The worktree is the blast radius.
      git -C "$wt" add -A
      git -C "$wt" diff --cached > "$outdir/diff.patch"

      # V1 runs before any model sees the artifact.
      ( cd "$wt" && eval "$v1cmd" ) > "$outdir/v1.log" 2>&1 \
        && echo "pass" > "$outdir/V1" || echo "fail" > "$outdir/V1"
      log "$cid/$fleet V1=$(cat "$outdir/V1") injection=$inj_status"

      echo "done" > "$outdir/STATUS"
      git -C "$repo" worktree remove --force "$wt" 2>/dev/null || true
    done
  done
}

# =================================================================== review ===
phase_review() {
  for cf in "${contract_files[@]}"; do
    cid="$(jq -r '.contract_id' "$cf")"
    [[ -n "$ONLY" && "$cid" != "$ONLY" ]] && continue
    objective="$(jq -r '.objective' "$cf")"

    for gen in W1 W2; do
      artdir="$ARTIFACTS/$cid/$gen"
      [[ -s "$artdir/diff.patch" ]] || { log "$cid/$gen: no artifact, skipping review"; continue; }

      for rev in W1 W2; do
        arm=$([[ "$gen" == "$rev" ]] && echo self || echo cross)
        out="$RESULTS/${cid}__gen-${gen}__rev-${rev}.json"
        if [[ -s "$out" ]]; then
          log "$cid gen=$gen rev=$rev ($arm): cached, skipping"
          continue
        fi

        # Build the review prompt from the ONE rubric, identical across all four arms.
        prompt="$artdir/review_prompt_${rev}.md"
        B0_OBJECTIVE="$objective" python3 - "$RUBRIC" "$artdir/diff.patch" "$artdir/v1.log" "$prompt" <<'PY'
import os, sys, pathlib
rubric, diff, v1, out = (pathlib.Path(p) for p in sys.argv[1:5])
text = rubric.read_text()

# Strip operator commentary. It describes the experiment; a reviewer that reads it is no
# longer blind to the self-vs-cross comparison it is participating in. Fail loudly rather
# than silently sending the header if the sentinel is ever removed.
SENTINEL = "<!-- ===== PROMPT BEGINS ===== -->"
if SENTINEL not in text:
    sys.exit(f"FATAL: {rubric} is missing the {SENTINEL!r} sentinel; refusing to send an unblinded prompt.")
text = text.split(SENTINEL, 1)[1].lstrip()

text = text.replace("{{OBJECTIVE}}", os.environ["B0_OBJECTIVE"])
text = text.replace("{{DIFF}}", diff.read_text()[:120_000])
text = text.replace("{{V1_REPORT}}", v1.read_text()[-4_000:] if v1.exists() else "(not available)")
out.write_text(text)
PY

        log "$cid gen=$gen rev=$rev ($arm) reviewing..."
        fleet_review "$rev" "$prompt" "$out" || log "$cid gen=$gen rev=$rev: review errored"
      done
    done
  done
}

case "$PHASE" in
  generate) phase_generate ;;
  review)   phase_review ;;
  all)      phase_generate; phase_review ;;
  *) die "unknown phase: $PHASE" ;;
esac

log "done. Now: ./benchmarks/b0/score_b0.py"
