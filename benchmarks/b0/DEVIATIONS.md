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
