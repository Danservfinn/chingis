# verify/ — the verification stack

The verifier is the ceiling. Under Rev G/H, verifier engineering is first-class work,
not overhead.

| Path | Tier | State |
|---|---|---|
| `rubric/review_rubric.md` | shared review prompt | **written** — used by B0, reused as the V2 prompt at B5 |
| `seeded/` | the control group | **written** — corpus classes + manifest. Write-protected. |
| `v1_runners.py` | V1 — pytest / ruff / build / diff-scope. Kernel-run, deterministic, free, always first | B5 |
| `v2_crossfleet.py` | V2 — opposite-fleet review | B5, **only if B0 passes** |
| `v3_spotcheck.py` | V3 — Sol max-reasoning / Claude, 5% sample | B5 |

**B5 acceptance:** seeded defects caught at the B0-measured rate, **not below**.

The structural rule: no artifact ships on self-review alone. Cross-fleet V2 exists because
a monoculture's blind spots grade themselves.

**Verifier collapse mode:** if V2 approval rates drift above ~98% for weeks, the reviewers
have gone soft or the prompts have converged. The seeded corpus is the only instrument
that can tell you which.
