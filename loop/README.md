# loop/ — the M-ladder era

**Empty by design.** Opens after B6 (from Oct 5). Weekly cycles.

One cycle = **act → log → consolidate → redeploy**.

| File | Holds |
|---|---|
| `score.py` | outcome scoring from V1 + V2/V3 verdicts |
| `consolidate.py` | advantage-filtered behaviour cloning + light RL prep |
| `train_qlora.py` | MLX QLoRA, ≤14B targets |
| `promote.py` | gate checks before redeploy |
| `proposals/staging.yaml` | the ratchet inbox — loop-writable |

## Write set

A cycle may modify **executive weights (LoRA adapters)** and **`policy/routing_policy.yaml`**.
Nothing else. `kernel/`, the capability registry, the meters, and `evals/` are read-only to
the loop at the filesystem level — see `ops/protected_paths.txt`. **The gate never learns.**

## Proposal ratchet

The loop may *propose* new action verbs, skills, tools, and eval tasks into
`proposals/staging.yaml`. The operator batch-approves; the schema version bumps; the cache
re-warms once. Growth is one-way, audited, and never self-authorized.

## Dates

First full cycle attempted by **Oct 11**. M0 read by **Nov 1**. **M3 verdict due Dec 1** —
three honest cycles. M3 fail is the kill criterion: write the null result, archive, keep
the kernel. A published null result is completion; silent abandonment is the only failure
mode the design prohibits.
