# M0 vs Chingis — the founding comparison, first execution (2026-08-17)

**The experiment the project's premise rests on had never been run. It now runs. Its
first data is a tie at n=4, far below the pre-registered bar, and the tool correctly
refuses to read it.**

The useful output of this run is not the number. It is that **four separate defects were
blocking the experiment**, each of which would have silently produced a wrong answer, and
none of which was visible until someone tried to execute it.

## Result

| Arm | Honest successes | Tokens | Successes / Mtok | Mean wall |
|---|---|---|---|---|
| **M0** (fixed routing, fixed retry, no executive) | 4 / 4 | 126.5k | 31.62 | 70.0s |
| **Chingis** (live executive, glm-4.6) | 4 / 4 | — | — | 44–74s |

```
M0's pre-registered bar needs >=60 contracts on the VARIABLE mix; Chingis has 4.
Too early to read.
```

That refusal is the correct output. Spec §12 pre-registered ≥60 contracts on the variable
mix, and it also pre-registered that **Chingis is expected to LOSE the routine mix** —
these four contracts are routine by construction, so a tie here is consistent with the
design and is not evidence for either arm.

**No dollar figure appears above, and that is deliberate.** See defect 4.

## Why it had never run

### 1. Outcomes were never recorded

`DecisionLog.record_outcome` existed and was unit-tested, and was **called from nowhere in
production**. The `outcomes` table had zero rows after months of work, so
`m0_control.py --compare` always read an empty Chingis arm. Fixed: `cli.record_outcomes`
writes one row per contract after every submit.

### 2. W3 contracts could not be stored at all

`contracts.fleet` shipped with `CHECK (fleet IN ('WR','W1','W2','W0'))`. The W3 lane was
added months later without touching it, so every W3 contract failed at INSERT — and since
`outcomes` has a foreign key onto `contracts`, no W3 result was recordable either. Fixed
by migration `002_lane_set.sql`.

### 3. The executive names every contract `c_0001`

Observed live, across four separate tasks. `contracts.id` is a PRIMARY KEY, so four tasks
**collapsed into one row**, each overwriting the last, and `outcomes` collapsed with it.
The first attempt at this comparison reported `Chingis has 1` when four had run.

Per-contract accounting was therefore capped at however many distinct names the model
happened to invent — not a number any experiment should depend on. Fixed by qualifying
the stored key with its task (`t_fe01:c_0001`), which is the right place for the fix: a
naming convention the executive must remember is one it will eventually forget.

### 4. Success-per-dollar was uncomputable — the metric M0 is built around

`raw_adapter` accumulated cost from `resp.usage.cost`, an **OpenRouter field the z.ai
endpoint does not return**. It read 0.0 on every call, so `usd` was always exactly zero,
and there is no price table anywhere in the repo.

So the spec's stated basis for the founding claim — success per dollar — could not be
computed, and would have silently reported every arm as infinitely efficient.

The fix records what the endpoint *actually* reports (prompt and completion tokens) and
derives dollars only where a price is configured. **Unknown cost stays `None`, never
`0.0`**: "free" and "unpriced" are different claims, and a zero divides into an infinite
success-per-dollar that hands a win to whichever arm nobody priced. The surviving
efficiency metric is per-token, which is fully determined by observation.

## Incidental observations, not results

- **The executive dispatched to `W0` once**, a lane that is down. An absent adapter
  produces a `policy_exception` it can route around, which is the designed behaviour —
  but it is the first recorded instance of the executive making a routing call and
  getting it wrong, and it is data about routing quality once there is enough of it.
- **Decisions logged went from 3 to 20.** M2 needs ≥1000. Before today the executive had
  made three decisions in the system's entire life.
- One submit (`t_fe04`) exited non-zero and is excluded rather than counted.
- M0 is not deterministic across runs: `c_0001` passed on one run and failed on another,
  and one contract took 406s against a 50–90s norm. Variance at n=4 is large.

## What this does not show

Nothing about whether an adaptive harness beats a fixed one. n=4, routine mix, tie. The
pre-registered bar is ≥60 on the variable mix, and the honest reading of this run is
that the instrument now works and has produced its first four points.
