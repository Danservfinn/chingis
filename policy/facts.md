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
| W3 | **Metered per token — real USD**, unlike the flat fleets | Fixed inner shell, not inspectable *in practice* (see below) | **Any of four labs.** dsh headless; route selected per contract as `model: "provider/model"` over `zai`, `anthropic`, `xai`, `deepseek`. Consumes **no plan quota** — this is the lane to reach for when quota is the binding constraint and dollars are not, and the reroute target when a fleet refuses. |
| W0 | Electricity | None | Local model (ollama/MLX). Summarize, triage, injection pre-screen, shadow scoring. **DISABLED by default since 2026-08-17** — opt in with `CHINGIS_W0=on`. While off, the lane is absent from the lane set and the injection screen runs its structural and heuristic layers only. |

## Quota

- GLM peak multiplier ≈ **3×** during 14:00–18:00 UTC+8 ≈ **02:00–06:00 America/New_York**.
- Quota, not dollars, is the scarce resource on both packaged fleets.
- **W3 spends dollars instead of quota.** That is what it is for: when a flat fleet is
  quota-blocked (W1 was, until ~2026-09-01) or is in its 3x peak window, W3 reaches the
  same class of model for money. Route deliberately — an unbudgeted W3 habit converts a
  fixed monthly cost into a variable one.
- Units per contract are **guessed until B4**. B4 replaces these with a week of observed
  usage. Treat any number here as a placeholder with wide error bars.
- `quota_threshold` fires on **burn-rate anomaly**, not just totals.

## Refusal tendencies

- Refusal tendencies per fleet are **unmeasured since 2026-08-17**: the W1/W2
  observations were made on the Claude Code lanes, which no longer exist.
- Refusal detection is heuristic per fleet — exit codes, phrase patterns, and the
  empty-diff-with-explanation shape. Every detection is logged for tuning.
- **W3 is now the ONLY reroute after a refusal**, and the next lab is a string in the
  contract's `model` field. With the Claude Code lanes gone, `W3 anthropic/...`,
  `W3 xai/...`, and `W3 deepseek/...` each need their own key set; none is today, so a
  refusal on WR or W3-zai currently has **nowhere to go but the operator**.

## Model tiers

| Tier | Use | Cost shape |
|---|---|---|
| glm-4.6 / glm-5.3 | **Executive of record**, via the Z.ai coding endpoint. Emits schema-valid decisions. `glm-4.6` is the live default. | Flat under the coding plan |
| Sol | Escalation rung — **no transport**. Not offered on the current Codex plan. Escalate to the operator instead. | n/a |
| Terra | Mid-tier worker duty on W1 | Flat under plan |
| GLM-5.3 | WR worker duty, W3 default route. **Reasoning model** — it spends completion tokens on reasoning before any content, so a small `max_tokens` returns empty content rather than a short answer. Absent from pi-ai's installed catalog, so `w3.cordis.yml` declares it. | Metered on WR and W3 |
| GLM-4.7 | Was W2's inner model. **W2 removed 2026-08-17.** Still reachable directly at the Z.ai Anthropic endpoint, and used as a second reviewer identity in B0.2. | Metered |
| Claude / Grok / DeepSeek | Reachable **only** through W3 today; each needs its own key set (`ANTHROPIC_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`). Unset keys are refused by the adapter before launch, so an unconfigured route is a lane outage, not a worker failure. | Metered |

## Network containment — what `net:none` actually means (2026-08-17)

- **WR is contained.** Its bash runs under `sandbox-exec` with `(deny network*)` when the
  contract grants no network. Enforcement is at the OS, below the command, so it holds
  against `curl`, `python`, `nc`, and whatever else a worker reaches for. If the platform
  cannot provide `sandbox-exec`, the lane **fails closed** — it refuses to run bash rather
  than running it unconstrained.
- **The grant is all-or-nothing.** `sandbox-exec` filters by operation, not by host, so
  `net:allowlist:a.example` opens **every** host. The contract schema is more expressive
  than the enforcement. Treat any non-empty allowlist as "this contract has the internet"
  and do not rely on the host list as a boundary.
- **The packaged lanes are NOT contained this way.** W1, W2, and W3 run their own inner
  shells; Chingis does not gate their tools. W3 inherits dsh's `workspace-write` sandbox
  (which does deny network escalation and does deny writes outside the worktree, though
  `/private/tmp` remains writable). W1/W2 have whatever Claude Code permits. A `net:none`
  on a packaged-lane contract describes intent, not enforcement.
- Before 2026-08-17 **none** of this was true: `net:none` was declared everywhere and
  enforced nowhere, and `Registry.check_net` was dead code. Any conclusion drawn before
  that date about a worker's network reach was unfounded.

## Open unknowns

Stated so the executive can reason about its own uncertainty rather than confabulate:

- Real quota units per contract on either packaged fleet — **unmeasured until B4**.
- Whether cross-fleet review actually beats self-review — **MEASURED 2026-08-17, and the
  answer was no.** B0 returned FAIL: +10.0 pp against a pre-registered ≥15 pp bar, n=10,
  McNemar p=1.0000, and the two directions disagreed in sign (W1-generated −20 pp,
  W2-generated +40 pp). **V2 is dropped. Verification is V1+V3.** The executive must not
  treat "a different fleet reviewed it" as signal, and must not propose V2 as a reflex.
- Whether cross-fleet review is *asymmetric* — the sign disagreement above is the live
  open question, and it is per-reviewer, not per-pair. Two labs cannot answer it.
- Whether W3's inner loop can be made observable — its session log currently carries the
  session header only, so the lane is opaque in practice. Unmeasured.
- Refusal rates per task type — unmeasured.

## Lane removal — 2026-08-17

The Claude Code lanes are gone at operator direction: **W1** (native Anthropic) and **W2**
(Claude Code → Z.ai). Both drove the same third-party CLI whose inner shell Chingis could
neither inspect nor pin, and W2's model was a mapping z.ai owned rather than one we chose
— it had been labelled `glm-5.2` while actually serving `glm-4.7`.

Consequences the executive should carry:

- The lane set is **WR, W3, W0**. Two workers and a local summarizer.
- **B0's numbers stay valid history and are no longer reproducible here**, because two of
  its three lanes no longer exist.
- **B0.2 cannot currently run.** Its reviewer identities were `glm-5.3`, `glm-4.7`, and
  Anthropic-via-W1. The first two are one lab; the third needs `ANTHROPIC_API_KEY`
  through W3. Two identities in one lab cannot separate reviewer identity from lineage.
- Anthropic, xAI, and DeepSeek remain reachable *in principle* through W3 — each is one
  key away, and no new adapter.

## Provider substitution — 2026-08-06

W1 was specified as the Codex fleet. That account is free-tier and its allowance is spent
until ~2026-09-01, so **W1 is now Claude Code headless against Anthropic**. Spec §5 already
names Claude as a V3 verifier, so this is a substitution rather than a new vendor.

Consequence worth carrying: any B0 number measures **Anthropic ↔ GLM**. If Codex is ever
restored as W1, the B0-measured catch rate is not a valid floor for that pairing and B5's
acceptance criterion would need a re-run, not a re-use.
