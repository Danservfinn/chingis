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
| W1 | Flat-rate (Claude subscription) | Fixed inner shell, not inspectable | **Anthropic.** Claude Code headless, native. Same tool as W2, different lab — the cross-fleet contrast varies only the lab. |
| W2 | Flat-rate (GLM Coding Plan) | Fixed inner shell, not inspectable | **Z.ai / GLM.** Claude Code headless → Z.ai endpoint. Quota is prompt-based with peak multipliers. |
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
| glm-4.6 / glm-5.2 | **Executive of record**, via the Z.ai coding endpoint. Emits schema-valid decisions. | Flat under the coding plan |
| Sol | Escalation rung — **no transport**. Not offered on the current Codex plan. Escalate to the operator instead. | n/a |
| Terra | Mid-tier worker duty on W1 | Flat under plan |
| GLM-5.2 | WR worker duty, W2 inner model | Metered on WR, flat on W2 |

## Open unknowns

Stated so the executive can reason about its own uncertainty rather than confabulate:

- Real quota units per contract on either packaged fleet — **unmeasured until B4**.
- Whether cross-fleet review actually beats self-review — **unmeasured until B0**.
  Until B0 reports, the executive should not treat V2 as established signal.
- Refusal rates per task type — unmeasured.

## Provider substitution — 2026-08-06

W1 was specified as the Codex fleet. That account is free-tier and its allowance is spent
until ~2026-09-01, so **W1 is now Claude Code headless against Anthropic**. Spec §5 already
names Claude as a V3 verifier, so this is a substitution rather than a new vendor.

Consequence worth carrying: any B0 number measures **Anthropic ↔ GLM**. If Codex is ever
restored as W1, the B0-measured catch rate is not a valid floor for that pairing and B5's
acceptance criterion would need a re-run, not a re-use.
