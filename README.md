# Chingis

An adaptive harness: a research platform for **verifier-bounded recursive self-improvement**.
Chingis is the executive that commands two hordes it did not raise — the Codex fleet and
the GLM fleet — and is permitted to grow only by what the verifier can measure.

- **Design spec:** [`docs/adaptive-harness-design.html`](docs/adaptive-harness-design.html) — Rev H, model-sovereign
- **Implementation plan:** [`docs/CHINGIS-implementation-plan.md`](docs/CHINGIS-implementation-plan.md)

Both are protected paths. See §7 write-set enforcement below.

---

## Status

| Step | Deliverable | State |
|---|---|---|
| §9 | Day-one scaffold | **done** |
| **B0** | Cross-fleet verification experiment | **apparatus ready — not yet run** (due Sun Aug 9) |
| B1 | Kernel skeleton | not started (due Sun Aug 16) |
| B2 | Lane adapters | not started |
| B3 | Executive v1 | not started — **stack freeze lifts here** |
| B4 | Routing policy | not started |
| B5 | Verification stack | not started |
| B6 | Logging + shadow | not started |

> **Ground rule 1 is binding: B0 before any harness code.** Every directory below that
> belongs to B1+ contains a `README.md` stating what it will hold and nothing else.
> `kernel/`, `adapters/`, `executive/`, and `loop/` are deliberately empty of implementation.
> If B0's cross-fleet margin fails to clear its pre-registered bar, V2 is dropped from the
> design and the dual-fleet topology is re-justified on routing grounds before B1 begins.

## Run B0

```bash
./benchmarks/b0/preflight.sh          # what's installed, what's missing, what it blocks
./benchmarks/b0/run_b0.sh --dry-run   # exercise the full pipeline, spend nothing
./benchmarks/b0/run_b0.sh             # the real thing: 40 generations, 80 reviews
./benchmarks/b0/score_b0.py           # -> results/scores.csv + results/report.md
```

Read [`benchmarks/b0/README.md`](benchmarks/b0/README.md) before running, and
[`benchmarks/b0/PREREGISTRATION.md`](benchmarks/b0/PREREGISTRATION.md) before reading results.
Thresholds may be adjusted *before* the experiment runs, never after.

## Write-set enforcement

`kernel/`, `verify/seeded/`, `evals/heldout/`, and `docs/` are outside the loop's write
access — enforced by permissions, not discipline.

```bash
./ops/permissions.sh install    # L0 git guard (installs the pre-commit hook)
./ops/permissions.sh status     # what's protected, at which layer
./ops/permissions.sh lock       # L2: chflags uchg for a cycle window
./ops/permissions.sh unlock     # operator-only, off-cycle
```

Committing under a protected path requires `CHINGIS_OPERATOR=1` in the environment,
inline, for that one commit — so every gate change shows up in git history.
The audit trail for the audit trail.

## Scope fence

No web UI. No multi-user anything. No memory/RAG store beyond SQLite. No agent framework
dependencies. No new models, providers, or hardware before B3. Rev H is spent — Rev I
requires a failed milestone or an M0-class empirical result.

## Data boundary

Personal projects only. Nothing employer-adjacent enters any fleet, ever.
