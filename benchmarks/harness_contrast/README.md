# B1H — harness contrast

**Question:** holding the model constant, does harness architecture change task success?

```sh
./benchmarks/harness_contrast/run_hc.py --arms WR,W3,KUBLAI
./benchmarks/harness_contrast/run_hc.py --arms W3 --limit 1        # cheap smoke
./benchmarks/harness_contrast/run_hc.py --arms KUBLAI --start 3 --tag kublai_b   # batching
```

## Why this design

B0 could not separate "different lab" from "different reviewer" because its two arms
varied both at once. This benchmark exists partly to not repeat that. Every arm runs
**the same model, at the same lab, through the same endpoint**:

| Arm | Harness | Model | Route |
|---|---|---|---|
| `WR` | Chingis' own kernel-gated tool loop | `glm-5.2` | z.ai coding endpoint |
| `W3` | dsh, packaged, headless | `glm-5.2` | z.ai coding endpoint via pi-ai |
| `KUBLAI` | Nous `hermes-agent`, `--profile kublai` | `glm-5.2` | `zai-coding` (verified in `~/.hermes/profiles/kublai/config.yaml`) |

So a difference between arms is attributable to the harness — the tool loop, the context
assembly, the retry behaviour — and not to the model behind it. Kublai's own default is
`glm-5.3`; it is pinned down to `5.2` with `-m` for the duration of one CLI call, which
changes nothing on disk.

## One verifier, three harnesses

Each arm produces **a diff and nothing else**. Every diff is then applied to an
identical, freshly rebuilt worktree on the operator's machine and judged by Chingis'
own `V1Runner` (`pytest` + `diff_scope`).

No arm verifies itself. No arm's own claim of success is consulted — Kublai's work
crosses the network as a patch, not as a sentence. A harness that announces "done" and
produced nothing scores exactly as a harness that produced nothing silently. Rebuilding
the worktree for verification also means no arm can pass on helpful untracked state it
left lying around.

`context_refs` is `src/**`, so `tests/**` is out of scope and `diff_scope` flags any diff
that edits a test. The cheapest way to pass every one of these tasks is to delete the
failing assertion; that route is measured, not trusted.

## The tasks

Five small repair jobs, each with a test that currently fails and must pass, plus tests
that already pass and must keep passing. The bugs are deliberately of five different
kinds — arithmetic, boundary, mutable default, control flow, unicode — because five
flavours of one mistake would measure one narrow competence and read as a harness
difference.

## Safety around Kublai

Kublai is a **live production agent in Telegram-only incident mode**. The runner:

- uses `hermes -z` (one-shot), which starts no gateway and sends no message
- never touches BlueBubbles, Signal, Telegram, launchd, provider config, or credentials
- pins the model per-invocation, writing nothing
- checks `127.0.0.1:8644/health` before and after, and **aborts** if the gateway is not
  `ok` rather than running against a production agent in an unknown state

## Apparatus defects found (read this before trusting any number)

This benchmark produced two false results before it produced a true one. Both are
recorded because a benchmark that hides its own repairs is worth nothing.

1. **`__pycache__` binary hunks (fixed).** A worker that runs the tests — exactly what a
   good worker does here — generates `.pyc` files. `git add -A` swept them into the diff
   as binary hunks, and `git apply` then rejected the *entire* patch with `cannot apply
   binary patch ... without full index line`. The first full W3 run scored **1/5** on
   this basis while having actually solved every task; with a `.gitignore` in the fixture
   it scores **5/5**. The fixtures were simply unrealistic — every real Python repo has
   that file. This is B0's unparseable-reviewer-output failure mode wearing new clothes:
   an apparatus defect that reads as incapability and biases exactly one direction.

2. **Provider throttling (handled, not fixed).** The z.ai coding endpoint returns
   HTTP 429 / 529 `code-1305` under the sustained load this benchmark is by construction
   (three arms × five contracts against one endpoint). Hermes' own overnight loop hit the
   same wall on 2026-08-12. A throttled arm never attempted the task, so scoring it as a
   miss would invent a result. Following B0's rule for failed injections, throttled runs
   are **excluded from the denominator and reported as excluded**, never counted as
   failures. `looks_throttled()` detects the markers; `ArmResult.excluded` carries it.

   Note the two harnesses do **not** share a credential: Hermes resolves `ZAI_API_KEY`
   through Bitwarden Secrets Manager while Chingis reads the environment. They therefore
   sit in different quota buckets, and one being throttled while the other is not says
   nothing about either harness.

## Reading the output

`n=5` per arm. **This is a smoke-scale contrast, not a powered experiment.** At this n a
one-task difference is noise; no threshold was pre-registered because none could honestly
be met at this size. It is a shakeout of the apparatus and a direction to look — never a
ranking, and never evidence that one harness is better than another.

The runner prints that caveat with every report, on purpose.
