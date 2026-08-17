# B1H — harness contrast, first run (2026-08-17)

**Bottom line: the benchmark ran clean and discriminated nothing, because the tasks are
too easy. Both fully-executed arms scored 100%. Kublai was blocked by provider
throttling on its own credential after one served task.**

No harness is ranked below. At this n, with this ceiling, none can be.

## Result

| Arm | Harness | Solved | n | Test-weakened | Median s |
|---|---|---|---|---|---|
| `WR` | Chingis, own kernel-gated tool loop | **5** | 5 | 0 | 33.2 |
| `W3` | dsh, packaged, headless | **5** | 5 | 0 | 19.8 |
| `KUBLAI` | Nous `hermes-agent`, `--profile kublai` | **1** | 1 served | 0 | 121.0 |

All three arms ran `glm-5.2` through the z.ai coding endpoint. Every diff was applied to
a freshly rebuilt worktree and judged by Chingis' `V1Runner` — no arm verified itself.

Per contract:

| Contract | WR | W3 | KUBLAI |
|---|---|---|---|
| `hc_01_median` | PASS | PASS | PASS |
| `hc_02_bsearch` | PASS | PASS | throttled |
| `hc_03_mutable_default` | PASS | PASS | throttled |
| `hc_04_retry_reraise` | PASS | PASS | not attempted |
| `hc_05_unicode_truncate` | PASS | PASS | not attempted |

## What this does NOT show

- **Not that W3 is faster than WR.** The medians differ (19.8s vs 33.2s) but the arms
  differ in transport and process startup as well as in harness, and n=5 with no repeats
  gives no variance estimate. This is a stopwatch reading, not a latency result.
- **Not that Kublai is slower.** Its single served task took 121s, but that is one
  observation, over SSH, on a machine doing other work.
- **Not that any harness is better.** 10 of 10 on the two complete arms is a ceiling
  effect. A benchmark where everyone scores 100% has no discriminative power, and that is
  the single most useful thing this run established.

## Findings

### 1. The task set has no headroom (the real result)

Five bugs of five different kinds — arithmetic, boundary, mutable default, control flow,
unicode — and two harnesses solved every one on the first attempt. For a contrast to
measure anything, tasks must be hard enough that competent harnesses sometimes fail.
These are not. A successor set needs multi-file changes, ambiguous requirements, tasks
requiring the worker to *run* the tests and iterate, and failure modes V1 can actually
distinguish.

### 2. Two apparatus defects, both of which would have produced false rankings

- **`__pycache__` binary hunks.** A worker that runs the tests generates `.pyc` files;
  `git add -A` swept them into the diff as binary hunks and `git apply` rejected the
  whole patch. The first full W3 run scored **1/5** on this basis while having actually
  solved every task. With a `.gitignore` in the fixture it scores **5/5**. Same shape as
  B0's "5 of 24 unparseable reviewer outputs": an apparatus defect that reads as
  incapability and biases one direction only.
- **Provider throttling.** Handled by exclusion from the denominator, per B0's rule for
  failed injections. A throttled arm never attempted the task; scoring it as a miss
  invents a result.

Reported because a benchmark that hides its own repairs is worth nothing. The first
number this apparatus produced was wrong, and the second was wrong for a different
reason.

### 3. Operational: Kublai's z.ai credential is rate-limited, and its own cron depends on it

The decisive test, run at the same moment against the same endpoint and the same model:

| Credential | Source | Result |
|---|---|---|
| Chingis' `ZAI_API_KEY` | MacBook environment | **served**, 24.6s |
| Hermes' `ZAI_API_KEY` | Bitwarden Secrets Manager, on the Mini | **HTTP 429** |

Same endpoint, same model, same minute — so this is not the endpoint being down, not the
model, and not the harness. It isolates to the credential/account bucket. Hermes also
returned `HTTP 529 [1305]` on its default `glm-5.3`, so it is not model-specific either.
Persisted across ~25 minutes of probing.

**This is worth the operator's attention independently of the benchmark.** Kublai's
overnight self-improvement cron (`dff4740cc8f4`, 03:00 daily) is pinned to
`zai-coding/glm-5.2`, which is exactly the path returning 429 — the same symptom the
Hermes wiki records for 2026-08-12. That job is very likely failing nightly right now.

## Provenance

Raw rows in this directory: `wr.json`, `w3.json`, `kublai_a.json` (throttled), and
`rows.json` (the one served Kublai task). `probe.json` / `probe2.json` are the
credential-isolation probes. Nothing here was edited by hand.
