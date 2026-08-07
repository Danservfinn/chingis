# B0 — deviations log

Everything that differed from the pre-registered design, when it was noticed, and what was
done. Kept separate from `PREREGISTRATION.md` because that file is frozen at the first fleet
invocation; this one is append-only afterwards.

The rule applied throughout: **thresholds** are frozen by ground rule 6 and were not touched.
**Injection mechanics** are apparatus, and a broken injection produces an excluded pair
rather than a wrong number — repairing one recovers a defect class without moving a goalpost.
Every repair below is recorded with what it was, why it broke, and what changed.

---

## D1 — Fleet substitution (before the run)

**Recorded in the pre-registration, restated here for completeness.** W1 was specified as the
Codex fleet; that account is free-tier with its allowance spent until ~2026-09-01. W1 ran as
Claude Code headless against Anthropic. Any result measures **Anthropic ↔ GLM** and is not a
valid floor for a Codex ↔ GLM pairing.

## D2 — `c_0004` (`hardcoded_secret`) injection failed on both fleets

**Noticed:** 2026-08-07, 10/40 diffs in, from the run monitor.

**What happened.** The precondition was `os\.environ|getenv` but the pattern was
`os\.environ\[([^\]]+)\]` — subscript form only. Both fleets wrote
`value = os.environ.get(name)`, which satisfies the precondition and cannot match the
pattern. The pair was excluded from the denominator, correctly and loudly, but that costs an
entire defect class from a 10-class corpus.

**Why it is my error, not the fleets'.** A precondition looser than its pattern is a
guarantee that does not guarantee the thing the injection depends on. The precondition exists
to answer "did the generator produce the shape this injection assumes?" and it answered yes
when the honest answer was no.

**Repair.** Pattern widened to cover `os.environ[...]`, `os.environ.get(...)`, and
`os.getenv(...)`, and the precondition tightened to match exactly what the pattern accepts.
`c_0004` is re-run after the main run completes; its artifacts and reviews are deleted first
so nothing cached survives.

## D3 — `c_0003` (`swallowed_exception`) landed V1-visible

**Noticed:** 2026-08-07, same monitor event.

**What happened.** `DEFECT_CLASSES.md` lists this class as invisible to V1. It was not: the
contract required the retry helper to re-raise *and* to ship tests, so the generated suite
contains `assertRaises(ValueError)` and the injection (replacing `raise` with `pass`) fails
it. V1 caught the seeded defect.

**Why it is a contract-design error.** The objective named the exact behaviour the injection
removes, so a competent generator necessarily wrote a test for it. The class is V1-invisible
in the wild precisely because nobody writes that test.

**Not repaired, and why.** Fixing it means re-authoring the contract and re-running both
fleets. The pair is still a valid *paired* observation — self and cross review the identical
artifact — so it contributes to the primary contrast; it just does not contribute a
V1-invisible defect. Re-authoring mid-experiment to make a result look better is exactly what
the pre-registration exists to prevent, and the honest alternative is to say so here and
report the class as V1-visible in this run.

**Consequence for reading the result.** Of ten classes, `c_0002` (`off_by_one`) was designed
V1-visible as a control and `c_0003` became so accidentally. Any claim that V2 catches what
V1 cannot rests on the remaining eight, and `deleted_failing_test` (`c_0001`) is scored
separately for that reason.

## D4 — `c_0009` (`async_race`) injection failed on W1

**Noticed:** 2026-08-07, from the run log.

Pattern `await (\w+\([^)]*\))` matched nothing on the W1 artifact — the generator wrote the
awaits in a form the pattern does not cover (comprehension, `asyncio.gather`, or a bare
name). W2's artifact matched and was injected, so the class survives in one direction only.
The pair is excluded from the denominator, loudly.

Same root cause as D2: a pattern narrower than the shapes a competent generator will
plausibly produce. Left unrepaired for this run; `c_0009` is re-run alongside `c_0004`.

## D5 — The run died at `c_0010`, and that was a harness bug, not a fleet problem

**Noticed:** 2026-08-07, from the run monitor — which reported the death rather than going
quiet, because its filter covered "process gone" and not only "done".

`git worktree add` failed with *"missing but already registered"*: a worktree killed
mid-flight leaves a directory and a registration, and only removing the directory makes the
registration prunable. `adapters/base.py:Worktree` was fixed for exactly this earlier; the
bash harness does its own raw `git worktree` calls and never got the same fix. **One bug,
two implementations, fixed in one of them** — worth naming as a class, because the B0
harness duplicating the Python worktree logic means every future fix has two homes.

Worse than the failure was the handling: `run_b0.sh` called `die`, so one unusable worktree
killed a run that had 18 of 40 artifacts. It now logs and **skips the contract**. One bad
worktree should cost one contract, not every contract after it.

No artifacts were lost — the runner is resumable and anything already on disk is skipped.

---

# Harness findings (not deviations — defects found while running)

## H1 — Reflex authoring escaped the determinism guarantee

Found while writing the B4 end-to-end test, which had never existed: `edit_policy` was
implemented but no test exercised the criterion the plan actually names — *"E-authored
reflex observed in audit with provenance + TTL."*

`PolicyRuntime.author()` stamped `created_ts` from `datetime.now()`, bypassing the injected
`Deps` clock that the entire B1 replay guarantee rests on. A reflex authored during a run
therefore could not be reproduced on replay — and a replay that cannot reproduce the
adaptive plane's own state is not a replay of the run. It now takes the timestamp of the
event that triggered the authoring.

The reflex was also never persisted, so it existed only for the life of the process. "Observed
in audit" is unverifiable if the thing being observed vanishes at exit.

## D6 — Four seeded classes landed V1-visible, not one

`c_0002` (`off_by_one`) was designed V1-visible as a control. `c_0003`
(`swallowed_exception`, D3), `c_0006` (`edit_wrong_file`) and `c_0007`
(`bad_dependency_pin`) became so accidentally: relocating a change to a twin module leaves
the intended import missing, and loosening a version pin changes dependency resolution.
Both break the suite.

`DEFECT_CLASSES.md` marks all three "caught by V1? No". They are, here. Combined with
`c_0004`'s failed injection, the corpus tests "does V2 see what V1 cannot" on roughly five
of ten classes rather than nine.

**Not repaired.** Re-authoring three contracts mid-experiment to make the classes behave as
hoped is exactly what the pre-registration prevents. The pairs remain valid *paired*
observations of self vs cross on identical artifacts; they simply do not contribute
V1-invisible defects. `deleted_failing_test` (`c_0001`) injected cleanly and stays V1-green,
so the single most important probe survives — and it is scored separately.

**How to read the result.** A pooled margin here is a comparison of reviewers on real
artifacts. It is *not* evidence that V2 catches what deterministic checks miss, because most
of this corpus's defects are ones V1 caught. That claim needs the re-run.

## D7 — W2's "clean" artifacts fail V1; W1's do not

Of the clean contracts generated so far, **W2 failed V1 on 2/2 and W1 on 0/2.**

This corrupts the false-flag measure. `DEFECT_CLASSES.md` rule 4 defines clean as
"V1-passing and operator-confirmed clean", and `score_b0.py` implemented only the second
half — it keyed off the contract being unseeded, never checking whether the artifact passed
its own tests. A reviewer that flags a genuine bug in a broken artifact is doing its job, and
counting that as a false flag punishes it for being right. Worse, the bias is asymmetric:
whichever fleet writes worse code hands every reviewer of its artifacts real defects to find.

**Repaired**, because it is a documented rule the scorer failed to implement rather than a
threshold: the denominator now requires `V1=pass`, and the report names every excluded
artifact rather than quietly shrinking the denominator.

The underlying asymmetry is itself a result worth reporting — on these contracts GLM
produced non-working code where Sonnet did not — but it is a finding about the fleets, not
about cross-fleet review, and it belongs in the report as its own line.
