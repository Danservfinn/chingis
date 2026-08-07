# CHINGIS — Implementation Plan
**Companion to:** Adaptive Harness System Design Spec, Rev H (model-sovereign)
**Date:** 2026-08-06 · **Owner:** Daniel · **Host:** MacBook Pro M2 Max 32GB

Chingis is the executive that commands two hordes it did not raise — the Codex fleet and the GLM fleet — and is permitted to grow only by what the verifier can measure.

---

## 0. Ground rules (binding, from Rev F/G)

1. **B0 before any harness code.** The cross-fleet verification experiment is the first deliverable. If V2 review doesn't beat self-review, stop and simplify.
2. **Timebox:** B0 complete by **Sun Aug 9**. B1 complete by **Sun Aug 16**. Two weekends, as committed.
3. **Stack freeze:** the choices in §1 are frozen until B3 is green. Rev H was the one audit-triggered revision; any Rev I requires a failed milestone or a B0/M0-class empirical result. A goal change requires a failed milestone.
4. **Write-set:** `kernel/`, `verify/seeded/`, `evals/heldout/`, and this plan are outside the loop's write access. Enforced by permissions (§7), not discipline.
5. **Data boundary:** personal projects only. Nothing employer-adjacent enters any fleet, ever.
6. **Pre-registration:** every threshold in this plan (B0 margin, M-ladder bars) may be adjusted *before* its experiment runs, never after seeing results.

---

## 1. Frozen stack

| Layer | Choice | Notes |
|---|---|---|
| Language / runtime | Python 3.12, `uv`, asyncio | No agent frameworks. The kernel **is** the framework; a framework would smuggle rigid orchestration back in. |
| Schemas | Pydantic v2 + JSON Schema | Decision + contract schemas versioned in-repo |
| Store | SQLite (WAL mode), one file: `chingis.db` | Events, decisions, contracts, outcomes, cycles |
| Executive (Phase 1–2) | GPT-5.6 **Luna** via OpenAI API, strict structured outputs, explicit cache breakpoint | Confidence-gated escalation to Sol |
| Executive (Phase 3+) | Local via MLX server: GPT-OSS 20B (co-resident) / Qwen3.6 35B-A3B (headless) | Exact model repos verified at B6, not before |
| Lane W1 (optional packaged tool) | Codex CLI, headless (`codex exec`) | E may select; ChatGPT plan; Sol/Terra per contract |
| Lane W2 (optional packaged tool) | Claude Code headless (`claude -p`) → Z.ai endpoint | E may select; GLM Coding Plan via its supported tool |
| Lane WR (raw, primary) | Direct API (GLM-5.2 / GPT-5.6 tiers / local) + Chingis-owned tool loop: `bash`, `read_file`, `apply_patch`, worktree-scoped | No inner harness; metered; first-class flywheel data |
| Fleet W0 | MLX/LM Studio local server | Summarize, triage, injection pre-screen, shadow |
| Training (loop era) | `mlx-lm` QLoRA | ≤14B targets |
| Ops | `launchd` (optional), `gtimeout` (brew coreutils), git hooks | |

> Flag names for `codex` and `claude` drift between releases — pin installed versions in `.tool-versions` during B2 and verify flags against `--help` then. Do not chase upgrades until B3.

---

## 2. Repository layout

```
chingis/
├── kernel/                  # FIXED PLANE — operator-writable only
│   ├── bus.py               # event loop, queue, seq numbering
│   ├── events.py            # event types (spec §2 table)
│   ├── capabilities.py      # token registry, scope checks
│   ├── meters.py            # $ budget, quota units, thresholds 60/85/100
│   ├── policy_runtime.py    # routing_policy.yaml executor + fast-paths
│   ├── audit.py             # append-only writer; hash chain per task
│   └── replay.py            # deterministic re-run from audit log
├── executive/
│   ├── prompts/system.md    # frozen prefix v1 (byte-identity test in CI)
│   ├── schema/decision.schema.json
│   ├── ledger.py            # sections, 3k-token cap, compaction
│   ├── client_luna.py       # cache breakpoint after prefix; strict outputs
│   └── client_local.py      # Phase 2+; Outlines-constrained decoding
├── adapters/
│   ├── base.py              # run(contract) -> Result; healthcheck()
│   ├── raw_adapter.py       # WR — model API + minimal tool loop (primary)
│   ├── tools/               # bash, read_file, apply_patch (capability-gated)
│   ├── codex_adapter.py     # W1 (optional packaged tool)
│   ├── glm_cc_adapter.py    # W2 (optional packaged tool)
│   └── local_adapter.py     # W0
├── contracts/
│   ├── contract.schema.json
│   └── store.py
├── verify/
│   ├── v1_runners.py        # pytest / ruff / build / diff-scope checks
│   ├── v2_crossfleet.py     # opposite-fleet review, rubric prompt
│   ├── v3_spotcheck.py      # Sol max-reasoning / Claude, 5% sample
│   └── seeded/              # WRITE-PROTECTED defect corpus + manifests
├── loop/                    # M-ladder era (post-B6)
│   ├── score.py             # outcome scoring from V1 + verdicts
│   ├── consolidate.py       # advantage-filtered BC + light RL prep
│   ├── train_qlora.py
│   ├── promote.py           # gate checks before redeploy
│   └── proposals/staging.yaml   # ratchet inbox (loop-writable)
├── policy/routing_policy.yaml   # ships EMPTY; executive-authored reflexes only
│                                #   (author + creating decision id + TTL); CI-enforced
├── evals/
│   ├── scenarios/           # 10 scripted orchestration scenarios (B3)
│   ├── heldout/             # WRITE-PROTECTED, operator-rotated monthly
│   └── dashboard.py         # sqlite -> static html agreement/eval board
├── benchmarks/
│   └── b0/                  # the first deliverable (see §5, Weekend 1)
├── ops/
│   ├── permissions.sh       # write-set enforcement
│   └── precommit.sh         # protected-path guard + reflex provenance check
├── .env.example             # OPENAI_API_KEY, ZAI_*, endpoints
└── chingis.db
```

---

## 3. Data model (SQLite)

```sql
events(id, task_id, seq, ts, type, payload_json, audit_hash)
decisions(id, event_id, ledger_hash, verb, reason_code, confidence,
          params_json, model, latency_ms, cost_usd)
contracts(id, task_id, fleet, spec_json, status, created_ts)
outcomes(contract_id, v1_pass, v1_detail_json, v2_verdict, v3_verdict,
         cost_usd, quota_units, wall_s, score)
cycles(id, started_ts, base_model, adapter_path, eval_json, promoted, notes)
quota_ledger(fleet, window_start, units_est, multiplier, source)
```

Every decision row is a future training example; every outcome row is its label. Schema changes are migrations, never in-place edits — the log is the research asset.

## 4. Core wire schemas

**Decision** (executive → kernel, strict): as in spec §3 — `decision`, `reason_code` (enum), `confidence`, `params`. Kernel re-validates; two invalid attempts ⇒ `decision_invalid` ⇒ forced `escalate`. Confidence < 0.6 ⇒ kernel forces `escalate` regardless of verb.

**Contract** (executive → adapter): as in spec §4 — `lane` (WR|W1|W2|W0), `model` (WR only; packaged lanes pick internally), `objective` (one paragraph, testable), `context_refs` (paths/globs, least privilege), `capabilities`, `output_schema`, `budget {wall_min, quota_units, tokens_usd, tool_calls, inlane_retries}`, `verification[]`.

**Adapter Result** (normalized across fleets):
```json
{ "status": "done|failed|refused|timeout",
  "artifacts": ["diff", "files", "stdout_tail"],
  "raw_cost": {"quota_units_est": 3, "wall_s": 412},
  "refusal_signal": null }
```
Refusal detection per fleet is heuristic (exit codes + phrase patterns + empty-diff-with-explanation shape); log every detection for tuning. A refusal is an event, never a dead end.

**Ledger** (`executive/ledger.py`), markdown sections, hard 3k-token cap:
`GOAL · PLAN · STATE · BUDGET/QUOTA · RECENT_OUTCOMES (last 5, one line each) · OPEN_QUESTIONS`. Crossing the cap forces `compress_ledger` before any other verb.

---

## 5. Build schedule

### Weekend 1 — B0: the experiment before the code (Aug 8–9, ~10h)
**No harness code.** Bash + markdown + a spreadsheet.

1. Author 20 contracts against 2–3 of your real repos (Fantasy Analyzer and parsethis are good sources — small, testable changes).
2. Seed 10 of them with known defects from the corpus classes: off-by-one, edit-wrong-file, deleted failing test, swallowed exception, hardcoded secret, perf regression, bad dependency pin, encoding bug, async race, doc/code contradiction. Manifest with exact locations goes in `verify/seeded/` (write-protected from day one).
3. For each contract, run four arms with a dumb bash script: generate-W1/review-W1 (self), generate-W1/review-W2 (cross), generate-W2/review-W2 (self), generate-W2/review-W1 (cross). Same review rubric prompt for all arms.
4. Score: seeded-defect catch rate and false-flag rate, self vs cross, both directions.
5. **Pre-registered go/no-go:** cross-fleet review must beat self-review by **≥15 percentage points** on seeded-defect catch. Pass → proceed. Fail → V2 is dropped from the design, verification simplifies to V1+V3, and the dual-fleet topology gets re-justified on routing grounds alone before B1.

Deliverable: `benchmarks/b0/report.md` — one page, one number, one verdict.

### Weekend 2 — B1: kernel skeleton (Aug 15–16, ~14h)
`bus.py`, `events.py`, `capabilities.py`, `meters.py`, `audit.py`, `replay.py`. No adapters, no executive — scripted fake events only.
**Acceptance:** pytest replays a 50-event scripted session byte-deterministically from the audit log; capability denial and 60/85/100 budget thresholds fire correctly.

### Weeks 3–7, evenings (~6–8h/wk)
| Step | Window | Build | Acceptance |
|---|---|---|---|
| B2 adapters | Aug 17–30 | All lane adapters behind `base.py` incl. WR raw tool loop (+~10h); result normalization; refusal heuristics; version pinning | One contract runs on any lane unmodified; WR passes worktree-containment tests |
| B3 executive v1 | Aug 31 – Sep 13 | Luna client, frozen prefix + cache breakpoint, strict schema, confidence gate, escalation to Sol | **All 10 scenarios green:** clean dispatch · fail→retry ok · fail×2→reroute · refusal→reroute · refusal×2→request_human · budget 85%→checkpoint/descope · verify_failed→revised retry · peak-window route override · outage→drain · ambiguous→request_human. **Stack freeze lifts here (target Sep 13).** |
| B4 policy | Sep 14–20 | `policy_runtime.py` + routing_policy.yaml (ships EMPTY — reflexes are executive-authored with TTL) + quota meters + facts file for E context | E-authored reflex observed in audit with provenance + TTL; quota calibration from a week of real usage replaces guessed units |
| B5 verification | Sep 21–27 |  V2 wired with the B0 rubric; V3 sampling at 5% | Seeded defects caught at the B0-measured rate, not below |
| B6 logging + shadow | Sep 28 – Oct 4 | Full decision logging; W0 shadow executive (GPT-OSS 20B, MLX server); dashboard; B0 bash harness formalized as the permanent **M0 control arm** | Agreement dashboard live; contract-volume tracker running; M0 baseline reproducible |

### M-ladder cadence (from Oct 5)
Weekly cycles. **First full act→log→consolidate→redeploy cycle attempted by Oct 11.** M0 read (Chingis vs. the frozen B0 control arm, ≥60 variable-mix contracts) by Nov 1. M1 read at ~100 held-out contracts. M2 at ≥1k logged decisions. **M3 verdict — three honest cycles — due Dec 1.** M3 fail = kill criterion: write the null result, archive, keep the kernel. M5 (post-teacher) only opens after M3 passes.

---

## 6. Key implementation specifics

**Executive prompt layout:** `[system.md — role, invariants, action semantics] + [decision.schema.json inlined] + ⟨cache breakpoint⟩ + [ledger] + [event]`. CI test asserts byte-identity of everything before the breakpoint across two cold builds. Schema changes only via the §10 ratchet: bump `schema_version`, one deliberate cache re-warm, migration note in the decisions table.

**Adapter invocations (verify flags at B2 against pinned versions):**
```bash
# W1
gtimeout "${WALL}s" codex exec --json --sandbox workspace-write \
  --cd "$WORKTREE" "$(cat contract_prompt.md)"
# W2
ANTHROPIC_BASE_URL="$ZAI_CODING_ENDPOINT" ANTHROPIC_AUTH_TOKEN="$ZAI_KEY" \
gtimeout "${WALL}s" claude -p --output-format json "$(cat contract_prompt.md)"
```
Each contract executes in a disposable git worktree; the diff is the artifact; the worktree is the blast radius.

**WR raw-lane loop (`adapters/raw_adapter.py`):** `messages → model (tool defs: bash, read_file, apply_patch) → kernel-gated execution in the worktree → append result → repeat` until the model emits done or exhausts `tool_calls` / `tokens_usd`. Providers: Z.ai standalone API for GLM-5.2 (metered, terms-clean) or OpenRouter for one key across hosts; GPT-5.6 tiers via OpenAI API. B2 containment test: an adversarial contract attempting to read outside the worktree must be denied by the capability check, not by model politeness.

**Injection containment path:** worker output → W0 summarize/screen (flag instruction-like content) → tagged summary + V1 facts into ledger. Raw worker text never enters the executive context. Anything W0 flags is quoted inert in an `UNTRUSTED` block, and the executive's system prompt names it as data.

**Quota accounting:** treat plan quotas like tokens. Estimate units/contract in week one by observation, store per-fleet multipliers (GLM 3× peak 14:00–18:00 UTC+8 ≈ 02:00–06:00 ET; re-verify monthly — promo multipliers expire), and let `quota_threshold` fire on burn-rate anomaly, not just totals.

**Held-out rotation:** monthly, operator moves ~20% of benchmark contracts into `evals/heldout/`, logs the manifest hash. The loop never trains on anything whose hash appears there.

## 7. Write-set enforcement (`ops/permissions.sh`)

Layered, weakest to strongest — run L1 minimum before the first cycle:
```bash
# L0 — git guard: pre-commit rejects changes under kernel/, verify/seeded/,
#      evals/heldout/ unless CHINGIS_OPERATOR=1 is set in the environment.
# L1 — POSIX: training/consolidation runs as user `khan`, which has
#      read-only perms on protected dirs (chmod -R o-w, group sans khan).
# L2 — macOS flags for cycle windows: chflags uchg on kernel/*.py
#      (operator lifts off-cycle with chflags nouchg).
```
Any gate change is operator-only, off-cycle, committed with `CHINGIS_OPERATOR=1`, and shows up in git history — the audit trail for the audit trail.

## 8. Not building (scope fence)

No web UI (dashboard is static HTML). No multi-user anything. No memory/RAG store beyond SQLite. No agent framework dependencies. No mini deployment until the laptop version survives a month. No new models, providers, or hardware before B3. Rev H is spent — Rev I requires a failed milestone or an M0-class empirical result.

## 9. Day-one checklist (tonight, <1h)

```bash
mkdir -p chingis/{benchmarks/b0,verify/seeded} && cd chingis && git init
uv init && cp .env.example .env   # add OPENAI_API_KEY, ZAI creds
brew install coreutils            # gtimeout
codex --version && claude --version  # pin both in .tool-versions
# then: draft the first 5 B0 contracts before closing the laptop
```

The khan rides Saturday. The first battle is twenty contracts and one catch-rate number.
