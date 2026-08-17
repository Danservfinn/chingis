# W3 / dsh integration — decisions and deviations

Append-only record of where `docs/plans/2026-08-17-dsh-integration.md` met reality and
what was done instead. The plan is left unedited as the frozen statement of intent, the
same way `PREREGISTRATION.md` is frozen and `DEVIATIONS.md` carries what actually
happened. Reading the plan without this file will mislead you: its Phase 2 was overtaken
by events between writing and execution.

---

## D1 — B0 ran and FAILED between the plan being written and executed

**Noticed:** at the start of execution, from `git status` showing an untracked
`benchmarks/b0/results/report.md`.

The plan's central premise was that B0 was *blocked* on a second lab until W1's Codex
quota reset (~2026-09-01), and that dsh unblocks it. That premise was already stale. B0
had been run — W1 substituted to Claude Code → Anthropic per `DEVIATIONS.md` D1 — and
returned **FAIL**: +10.0 pp against a pre-registered ≥15 pp bar, n=10, McNemar
p = 1.0000, with the two directions disagreeing in sign.

**Consequence.** The plan's Phase 2 ("B0, unblocked") is void. Phase 3 (a "council"
scaling pairwise cross-fleet review from a pair to a quorum) is void with it: it assumed
the pairwise result held, and it did not. Building a council on a rejected hypothesis
would be the "qualified pass is not a pass" error B0's own report warns about.

**What was NOT done, deliberately:** W3 was not used to re-run B0. Four labs make it easy
to keep drawing samples until a margin clears 15 pp, and that is exactly the goalpost-move
the pre-registration exists to prevent. B0's verdict stands. V2 stays dropped, and
`verify/v2_crossfleet.py` still refuses to wire (`test_v2_refuses_to_run_without_a_b0_pass`).

## D2 — W3's justification changed from verification to routing

The plan sold W3 as "a second lab, and therefore better verification." B0 rejected the
premise underneath that. The report states the surviving justification itself: *"the
dual-fleet topology must now be re-justified on routing grounds alone — quota arbitrage
and refusal rerouting."*

W3 is now built and documented on exactly those grounds — metered billing beside the flat
fleets, and a four-lab reroute target behind one adapter — and is documented as **not a
verification lane**. The code says so, `policy/facts.md` says so, and `cli.py` says so at
the registration site.

## D3 — the plan named the wrong PyPI package (supply chain)

Plan Task 0.2 said `uv add --optional dsh deepseek-harness`, with a note to verify the
name first. That note earned its keep: **`deepseek-harness` on PyPI is not DeepSeek's
package.** It is an unrelated third party's project (`github.com/HenryZ838978/...`, a
17 KB wheel), and installing it would have pulled an unvetted dependency into the repo
under a name that looks official. It was not installed.

The genuine article is **`deepseek-harness-sdk`** (verified via its PyPI `project_urls`
pointing at `github.com/deepseek-ai/deepseek-harness`). It was not used either — see D4.

## D4 — transport is the CLI, not the Python SDK

The plan specified the Python SDK (`DeepSeekHarness(...)`). The adapter drives
`dsh --profile headless` as a subprocess instead:

- It is what every other packaged lane already does (`claude_adapter`, `codex_adapter`,
  `glm_cc_adapter` all shell out), so W3 reads like its siblings rather than introducing
  a second integration style.
- It adds **zero** Python dependencies. The real SDK is a developer-preview package that
  would buy nothing this lane needs.
- `dsh --profile headless "job"` runs one session, prints the final answer, and exits —
  already the exact shape `Result` wants.

## D5 — the lane vendors its own Node runtime

dsh imports `createZstdDecompress`, added in Node 23.8/22.15. This machine's Homebrew
node is **23.7.0**, and dsh fails at plugin load with a bare `SyntaxError`.

Rather than `brew install node@24` — mutating the operator's global toolchain to satisfy
one lane — the lane vendors **Node v24.19.0 LTS** under `fleets/dsh/.runtime/`, gitignored,
and `dsh_adapter.py` prepends it to `PATH`. The lane declares its own runtime; nothing
else on the machine changes. Reinstall instructions are in `README.md`.

## D6 — "glass box" did not survive contact with measurement

The plan justified W3 partly on transparency: dsh is open source and persists session
JSONL, so "every step is inspectable." **Measured, this is false in the stock headless
composition.** Decompressing the session file after a successful run yields exactly one
record:

```json
{"type":"session","version":0,"id":"session-…","createdAt":…,"cwd":"…","delegationDepth":0}
```

No messages, no tool calls. The lane is *auditable in principle* and *opaque in practice*,
and is documented and treated as opaque — judged by its diff, its prose screened at the
event boundary, exactly like W1 and W2.

This is recorded rather than quietly dropped because the failure mode is specific and
tempting: "the harness is open source" is a fact about the software, and it reads like
evidence that a run was observed. It is not. Making the inner loop observable is real
work that has not been done; it is listed in `policy/facts.md` under Open unknowns.

## D7 — Phase 4 (WS, the sovereign lane) cannot open on its own terms

The plan gated WS on "the B0 verdict plus one clean V2/V2.5 cycle." B0 failed and V2 was
dropped, so the gate was written against a verification stack that no longer exists.

WS was **not built**. `loop/ws_gate.py` was built instead: an executable refusal that
names every unmet condition and states that opening the lane requires a *new*
pre-registration rather than an edit to that file. It reads no environment variable and
takes no `force=` argument, and a test asserts their absence over the AST — a gate with a
bypass is not a gate.

The reasoning, in short: bounded self-improvement requires the bound to be a verifier the
improving thing does not control. What survives B0 is V1 (mechanical) and V3 (sampling).
A tool the executive wrote passes the tests it ships with, so V1 says PASS; V3 does not
review design. Nothing left reads a design, so nothing can currently judge a self-authored
tool. That is the honest state, and the gate says it out loud.

## D8 — Phase 5's ratchet kill-criterion was worth building early

The plan filed "any proposal whose diff touches protected paths → auto-reject at staging"
under Phase 5, behind the WS work. It was implemented now instead, because it is
independent of WS and the gap was real: `loop/ratchet.py` had **no** protected-path check,
so a proposal to edit `kernel/screen.py` would have sat in the queue as an ordinary
pending item awaiting a yes. Only the git hook stood behind it, and a queued proposal only
has to be granted once to be permanent.

The prefix list is read from `ops/protected_paths.txt` rather than copied, so the hook and
the ratchet cannot drift. Refusals are counted as gate probes stored beside the queue: one
is noise, a pattern of them is a finding about the executive.

## D9 — a post-hoc re-reading of B0, filed as a question and not a result

Regrouping B0's own 20 reviews by *who reviewed* rather than *whether it crossed*
reproduces every published figure and splits the data far more sharply:

| Grouping | Arm A | Arm B | Split |
|---|---|---|---|
| Lineage (B0's variable) | self 8/10 = 80% | cross 9/10 = 90% | +10.0 pp |
| Reviewer identity | GLM reviewing 7/10 = 70% | Anthropic reviewing 10/10 = 100% | **+30.0 pp** |

One factor — reviewer identity — also explains B0's sign disagreement, which the lineage
model cannot: W1-generated artifacts crossed *away* from Anthropic (−20 pp) and
W2-generated crossed *toward* it (+40 pp).

**Filed as a hypothesis, not a finding.** It is post-hoc, n=10 per arm, B0's McNemar test
could not separate even the larger split from zero at that n, and 5 of 24 reviewer outputs
were unparseable in a way that need not have fallen evenly across reviewers. Nothing in
the codebase acts on it.

`benchmarks/b02/PREREGISTRATION.md` drafts the replication that could earn it belief — a
factorial generator × reviewer design with ≥3 lineages, which is the thing W3 newly makes
possible and which two lanes cannot do by construction. **Its thresholds are left blank as
`OPERATOR MUST SET`.** Choosing a bar is the operator's act, and choosing one after seeing
the numbers that suggested the hypothesis would be worthless.

## D10 — plan items deliberately NOT built, so the ledger balances

An auditor should be able to account for every task in the plan. These were skipped on
purpose, and each would have been actively wrong to build:

| Plan item | Status | Why |
|---|---|---|
| 0.2 `uv add --optional dsh deepseek-harness` | **not done** | Wrong and unsafe package (D3); transport is the CLI (D4), so there is no Python dependency to add. `pyproject.toml` is untouched. |
| 2.3 extend `v2_crossfleet.OPPOSITE` with W3 | **not done** | V2 is dropped. Extending the reviewer map of a module that must never wire would make a dead module look maintained, and would leave a W3 reviewer assignment sitting there for someone to switch on. |
| Phase 3 council (`verify/v25_council.py`) | **not built** | It scaled a pairwise result B0 rejected. A quorum of reviewers does not repair a hypothesis that failed its bar; it launders it. The successor question is drafted in `benchmarks/b02/` instead. |
| Phase 4 `ws.cordis.yml`, `ws_adapter.py`, `plugin_proposal.py` | **not built** | Gate closed (D7). Building the lane behind a closed gate leaves a loaded mechanism whose only remaining safeguard is that nobody edits one file. |
| Phase 5 kill criterion: council catch-rate floor | **not built** | Guards a council that does not exist. |
| Phase 5 kill criterion: 3 consecutive negative held-out cycles | **not built** | The M-ladder is scaffolded and unexercised; there is no cycle to count yet. Belongs with the work that opens it, not ahead of it. |
| Phase 5 kill criterion: W3 writes outside its worktree → halt | **not built as a check** | It would test dsh's sandbox, and this integration deliberately does not rely on that sandbox. Chingis' containment is the worktree and the diff, which the kernel already owns for every lane. What IS asserted is Chingis-side: that the adapter roots the process at the worktree and puts session state outside it (`tests/test_w3_dsh_adapter.py`). |

The two Phase 5 items that did not depend on any of the above — the W3 arm in the M0
control and the ratchet's protected-path refusal — were built (D8).
