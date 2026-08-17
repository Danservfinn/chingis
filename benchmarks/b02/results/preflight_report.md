# B0.2 §4.1 parse-rate gate — PASSED (2026-08-17)

**All three reviewer identities honour the rubric's output contract at 100%. The gate is
open. No scored data has been collected.**

| Reviewer identity | Reached via | Lab | Parse rate | §5 floor (90%) |
|---|---|---|---|---|
| `glm-5.3` | z.ai coding endpoint | z.ai | **5/5 = 100%** | admitted |
| `glm-4.7` | z.ai Anthropic endpoint | z.ai | **5/5 = 100%** | admitted |
| `claude-sonnet` | Anthropic native, Claude Code subscription | Anthropic | **5/5 = 100%** | admitted |

Run on five **throwaway** artifacts (off_by_one, swallowed_exception, hardcoded_secret,
encoding_bug, and one clean change) that are deliberately not part of the scored corpus.
Excluding a reviewer is only an honest act *before* scored data exists; that is the whole
reason this gate runs first.

## Why this gate exists

B0's largest apparatus defect was that **5 of 24 reviewer outputs were unparseable** and
were scored as zero findings — a silent, one-directional downward bias on the catch rate.
B0's own report flagged it: *"if this is more than a couple, the rubric's output contract
is failing and the catch rates are biased downward."* That is 79% parseable, and B0 called
it broken; the §5 floor of 90% is derived as the smallest round number above what B0
itself rejected.

B0.2 adds reviewer identities the rubric had never been exercised against, so the risk
went **up**, not down. It did not materialise: 15 of 15 outputs parsed.

## Secondary observation, not a result

Every reviewer returned `0 findings, approve` on the clean artifact. That is the
false-flag behaviour B0 could not measure at all (it reviewed zero clean contracts), and
it is mildly encouraging — but n=1 clean artifact per reviewer is a smoke check, not the
false-flag denominator §5 requires. The scored run still needs its 1:2 clean:defected
ratio.

The gate measured **only parseability**, never correctness. Reading whether a reviewer was
*right* here would be reading the outcome before the experiment starts.

## What this does not clear

The gate is one of two §4.1 repairs. The other — clean artifacts in the corpus so the
false-flag guardrail has a denominator — is a corpus change and is not built yet.

## Standing limit on the design

`xai` and `deepseek` are unreachable (no `XAI_API_KEY`, no `DEEPSEEK_API_KEY`), so the
four-lab version is not runnable. Three identities across **two labs** is what the
credentials support. That is enough to separate reviewer identity — the actual question —
but it is *not* enough to cleanly re-test the lineage question, and any result must
restate that limit rather than let it be forgotten.

## Provenance

`preflight_parse.json` holds every row. Regenerate with
`./benchmarks/b02/preflight_parse.py`; pass reviewer names to run a subset.
