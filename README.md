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
| **B0** | Cross-fleet verification experiment | **RUN 2026-08-17 — verdict FAIL.** +10.0 pp against a pre-registered ≥15 pp bar, n=10, McNemar p=1.0000, directions disagree in sign. **V2 is dropped; verification is V1+V3.** See `benchmarks/b0/results/report.md` |
| B1 | Kernel skeleton | **done** — 50-event byte-deterministic replay green |
| B2 | Lane adapters | **done** — WR verified live; containment tests green |
| B3 | Executive v1 | **done** — all 10 scenarios green against a scripted executive |
| B4 | Routing policy | **done** — executive-authored reflexes, TTL, provenance |
| B5 | Verification stack | **V1 live; V2 DROPPED — B0 failed and `require_b0_pass()` refuses; V3 sampling live** |
| B6 | Logging + shadow | **done** — decision log, shadow scoring, static dashboard |
| §7 containment | Injection containment path: W0 screen → UNTRUSTED block | **done** — raw worker text cannot reach the executive |
| §3 persistence | `contracts/store.py`, on-disk `chingis.db` | **done** |
| §10 ratchet | `loop/proposals/staging.yaml` + `loop/ratchet.py` | **done** — loop may propose, only the operator may adopt |
| Phase 3 | `executive/client_local.py`, grammar-constrained decoding | **done** — unexercised, no MLX server running |
| M-ladder | `loop/` scoring, consolidation, promotion gate | scaffolded, unexercised (opens post-B6) |

**186 tests pass.** `uv run pytest -q`

> **Ground rule 1 said B0 before any harness code.** B1–B6 were built first anyway, on
> explicit operator instruction, while B0 was blocked on plan quota. B0 has since run and
> **failed**. The substrate did not depend on its verdict; V2 did, and
> `verify/v2_crossfleet.py` now refuses on the FAIL rather than on the absence of a report.
> The experiment did its job: a pre-registered consequence was paid.
>
> The live open question is not "does crossing help" but the **sign disagreement** B0's
> own guardrail caught. Regrouping its 20 reviews by *who reviewed* rather than *whether
> the review crossed* reproduces every published figure and splits the data three times as
> sharply — lineage +10.0 pp, reviewer identity +30.0 pp. That is post-hoc and
> underpowered, so it is filed as a hypothesis, not a result:
> `benchmarks/b02/PREREGISTRATION.md` drafts the replication, with every threshold left
> blank as `OPERATOR MUST SET`.

## Injection containment (spec §7)

The design's named novel attack surface is **orchestration hijack** — injected text that
persuades the *executive*, not a worker. `kernel/screen.py` closes that path, and it lives
in the kernel rather than the executive because it is a guarantee, not a preference:

```
worker output → W0 summarize/screen → tagged summary + V1 facts → ledger
```

Three layers, in this order: **structural** (worker text is summarized to shape only and
wrapped before it can reach a model context), **heuristic** (instruction-shaped content
flagged offline, always), **semantic** (W0 summarizes when a local model is available).
Layer 1 holds when 2 and 3 both miss.

Everything worker-influenced is screened at the event boundary — adapter summaries,
refusal signals, adapter errors, and V1 stdout tails. All four previously reached the
executive raw. What it sees now is `worker output: 350 chars, 6 lines, 4 non-blank` plus
V1's pass/fail facts; flagged content is quoted inert in an `UNTRUSTED` block that a worker
cannot close from inside.

## What is actually blocked

| Lane | Transport | State |
|---|---|---|
| **WR** (raw, primary) | Z.ai coding endpoint, `ZAI_API_KEY` | **live** — verified end-to-end: real model, kernel-gated tool loop, real worktree, V1 green |
| **W2** (GLM fleet) | Claude Code headless → Z.ai Anthropic endpoint | wired; unexercised |
| **W1** (second lab) | Claude Code headless → Anthropic, native | **live** — substituted for the Codex fleet, whose free-tier allowance is spent until ~2026-09-01. B0 ran on this substitution, so its numbers measure **Anthropic ↔ GLM** (`DEVIATIONS.md` D1) |
| **W3** (dsh fleet) | `dsh --profile headless` → pi-ai → `zai` \| `anthropic` \| `xai` \| `deepseek` | **live** — verified end-to-end 2026-08-17 on `zai/glm-5.2`: real diff, V1 green, screen held, replay intact. Only `ZAI_API_KEY` is set; the other three routes are a key away |
| **W0** (local) | MLX / LM Studio | no server running |

**W3 is a routing lane, not a verification lane.** B0 rejected cross-fleet review, and a
third fleet does not revive it. It exists for what B0's report says survives — quota
arbitrage (metered, so a quota-blocked or peak-window contract has somewhere to go) and
refusal rerouting (the next lab is a string in the contract's `model` field). Its inner
shell is open source but **opaque in practice**: the stock session log carries the session
header only, so it is judged by its diff like every other packaged lane. See
[`fleets/dsh/README.md`](fleets/dsh/README.md) and [`fleets/dsh/DECISIONS.md`](fleets/dsh/DECISIONS.md).

B0 ran on the W1 substitution above, so its result is Anthropic ↔ GLM and would not be a
valid floor for a Codex ↔ GLM pairing if Codex is ever restored.

**The same trap still applies to any successor experiment, and now has a new mouth.** W3's
default route is `zai` — the *same lab* as W2. A W2/W3 pairing therefore varies the shell,
not the lab, and reading "two lanes" as "two labs" would measure GLM reviewing GLM and call
it cross-review. `test_w3_default_route_does_not_impersonate_a_second_lab` pins this.

Everything else runs on Z.ai today: the executive (glm-4.6 / glm-5.2 via the coding
endpoint), the WR raw lane, W2, and W3's default route.

Also worth knowing: the Codex endpoint offers `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`,
`gpt-5.4-mini`, and `codex-auto-review`. **Sol is not among them**, so the spec's
Luna→Sol escalation rung has no transport on this plan; `escalate` currently has to target
Terra or the operator.

## Run a task

The operator surface (spec §1, `OP`):

```bash
uv run ./cli.py health                     # what is reachable right now
uv run ./cli.py submit "objective" --repo /path/to/repo
uv run ./cli.py status                     # decisions logged, chain integrity per task
uv run ./cli.py dashboard                  # regenerate the static eval board
```

The **operator names the repo; the executive names the objective and the lane.** A model
that could choose its own worktree root would be choosing its own containment.

An absent adapter produces a `policy_exception` the executive can route around — strictly
better than a lane that accepts dispatches and always fails. `cli.py health` is the
authority on what is reachable right now; W0 is the only lane currently down.

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
