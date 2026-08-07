# Facts for the executive's context

**These are facts, never rules.** They are supplied to the executive as context so it can
route by live judgment. Whether any of them becomes a reflex is the executive's call —
and when a provider changes a multiplier, updating a fact beats hunting down a hard-coded
rule. (Spec §6)

> Pricing and model facts are as of **2026-08-06**. Re-verify before committing spend.
> Quota multipliers are promotional and expire — re-verify monthly.

## Lane economics

| Lane | Billing | Opacity | Notes |
|---|---|---|---|
| WR | Metered per token | None — every step is in the audit log | The faithful path. Chingis-owned tool loop: `bash`, `read_file`, `apply_patch`, worktree-scoped. |
| W1 | Flat-rate (ChatGPT plan) | Fixed inner shell, not inspectable | Strong repo/diff workflow. Expect safety-stack refusals on security-flavored tasks. |
| W2 | Flat-rate (GLM Coding Plan) | Fixed inner shell, not inspectable | 1M-context inner model. Quota is prompt-based with peak multipliers. |
| W0 | Electricity | None | Local MLX. Summarize, triage, injection pre-screen, shadow scoring. |

## Quota

- GLM peak multiplier ≈ **3×** during 14:00–18:00 UTC+8 ≈ **02:00–06:00 America/New_York**.
- Quota, not dollars, is the scarce resource on both packaged fleets.
- Units per contract are **guessed until B4**. B4 replaces these with a week of observed
  usage. Treat any number here as a placeholder with wide error bars.
- `quota_threshold` fires on **burn-rate anomaly**, not just totals.

## Refusal tendencies

- W1 (Codex/Sol stack): refuses on crypto-adjacent and security-flavored material.
  Expected background noise, not a failure. One hop to reroute.
- Refusal detection is heuristic per fleet — exit codes, phrase patterns, and the
  empty-diff-with-explanation shape. Every detection is logged for tuning.

## Model tiers

| Tier | Use | Cost shape |
|---|---|---|
| Luna | Routine dispatch / retry / verify decisions, minimal reasoning, cached prefix | ~$0.002/decision, target < 2s |
| Sol | `revise_plan` / `edit_policy` / post-failure strategy, high reasoning | Seconds-to-minutes; rare by design |
| Terra | Mid-tier worker duty on W1 | Flat under plan |
| GLM-5.2 | WR worker duty, W2 inner model | Metered on WR, flat on W2 |

## Open unknowns

Stated so the executive can reason about its own uncertainty rather than confabulate:

- Real quota units per contract on either packaged fleet — **unmeasured until B4**.
- Whether cross-fleet review actually beats self-review — **unmeasured until B0**.
  Until B0 reports, the executive should not treat V2 as established signal.
- Refusal rates per task type — unmeasured.
