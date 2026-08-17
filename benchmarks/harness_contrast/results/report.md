# B1H — harness contrast, first complete run (2026-08-17)

**Bottom line: three independent harnesses, same model, 15 of 15. The benchmark ran
clean and discriminated nothing, because the task set has no headroom. No harness is
ranked here, because at this ceiling none can be.**

## Result — complete

| Arm | Harness | Solved | Test-weakened | Median s | Total s |
|---|---|---|---|---|---|
| `WR` | Chingis, own kernel-gated tool loop | **5 / 5** | 0 | 33.2 | 167.5 |
| `W3` | dsh, packaged, headless | **5 / 5** | 0 | 19.8 | 122.0 |
| `KUBLAI` | Nous `hermes-agent`, `--profile kublai` | **5 / 5** | 0 | 53.0 | 243.0 |

All three ran `glm-5.2` through the z.ai coding endpoint (`zai-coding`, verified in
`~/.hermes/profiles/kublai/config.yaml`). Every diff was applied to a freshly rebuilt
worktree and judged by Chingis' `V1Runner`. No arm verified itself.

| Contract | WR | W3 | KUBLAI |
|---|---|---|---|
| `hc_01_median` | PASS | PASS | PASS |
| `hc_02_bsearch` | PASS | PASS | PASS |
| `hc_03_mutable_default` | PASS | PASS | PASS |
| `hc_04_retry_reraise` | PASS | PASS | PASS |
| `hc_05_unicode_truncate` | PASS | PASS | PASS |

Zero test-weakening across 15 runs, with `tests/**` out of `context_refs` and
`diff_scope` watching. The cheapest way to pass any of these was to delete the failing
assertion. Nothing took it.

## The finding: no headroom

Five bugs of five deliberately different kinds — arithmetic, boundary, mutable default,
control flow, unicode — and three architecturally unrelated harnesses solved every one on
the first attempt. A benchmark on which everyone scores 100% cannot rank anything, and
establishing that is the single useful output of this run.

What a successor set needs: multi-file changes; requirements ambiguous enough that
interpretation differs; tasks that force the worker to *run* the tests and iterate rather
than pattern-match a one-line fix; and failure modes `V1` can actually separate. Until
then this apparatus measures that a harness is wired up, not that it is good.

## What this run does NOT show

- **Not a speed ranking.** The medians differ (W3 19.8s, WR 33.2s, Kublai 53.0s) but the
  arms differ in transport, process startup, and — for Kublai — an SSH round trip to a
  machine doing other work. n=5, no repeats, no variance estimate. It is a stopwatch
  reading, not a latency result.
- **Not that the harnesses are equivalent.** They were never separated. An untaken
  measurement is not a null result.

## Apparatus defects found, both of which would have published false rankings

1. **`__pycache__` binary hunks (fixed).** A worker that runs the tests generates `.pyc`
   files; `git add -A` swept them into the diff as binary hunks and `git apply` rejected
   the *entire* patch. W3's first full run scored **1/5** on that basis while having
   solved every task; with a `.gitignore` in the fixture it scores **5/5**. Same shape as
   B0's "5 of 24 unparseable reviewer outputs" — an apparatus defect that reads as
   incapability and biases one direction only.

2. **Provider throttling (handled).** Excluded from the denominator per B0's rule for
   failed injections, never scored as a miss. This mattered: an early Kublai batch
   returned 0/3 purely from HTTP 429, and on the retry after recovery the same three
   contracts returned **3/3**. Had those been recorded as failures, the published table
   would have shown Kublai at 1/5 against two perfect arms — a fabricated ranking from an
   environmental condition.

Recorded because a benchmark that hides its own repairs is worth nothing. The first
number this apparatus produced was wrong, and the second was wrong for a different reason.

## Operational note: Kublai's z.ai credential was rate-limited, and recovered

During the first attempt, at the same minute against the same endpoint and model:

| Credential | Source | Result |
|---|---|---|
| Chingis' `ZAI_API_KEY` | MacBook environment | served, 24.6s |
| Hermes' `ZAI_API_KEY` | Bitwarden Secrets Manager, on the Mini | **HTTP 429** |

Hermes also returned `HTTP 529 [1305]` on its default `glm-5.3`, so it was not
model-specific, and it persisted across ~25 minutes of probing. It has since cleared —
the retry ran 5/5 with median 53.0s, versus 121s for the single task that squeezed
through while degraded.

Still worth the operator's attention: Kublai's overnight self-improvement cron
(`dff4740cc8f4`, 03:00 daily) is pinned to `zai-coding/glm-5.2`, the exact path that was
returning 429, and the Hermes wiki records the same symptom on 2026-08-12. A recurring
throttle on that credential would fail that job silently and nightly.

## Safety

Kublai is a live agent in Telegram-only incident mode. Every run used `hermes -z`
one-shot: no gateway started, no message sent, no launchd, provider, or credential state
touched. `127.0.0.1:8644/health` returned `ok` before and after every batch.

## Provenance

`wr.json`, `w3.json`, `kublai_a.json`, `kublai_b.json` are the raw rows;
`probe.json` / `probe2.json` are the credential-isolation probes. `rows.json` holds the
single degraded-endpoint Kublai task from the first attempt, superseded by `kublai_a`.
Nothing was edited by hand.
