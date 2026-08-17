# DSH Integration ("The Third Horde") Implementation Plan

> ## ⚠ SUPERSEDED IN PART — read `fleets/dsh/DECISIONS.md` first
>
> **Banner added 2026-08-17 after execution. Nothing below it has been edited.** This
> document is the frozen statement of *intent*, kept unrevised the way
> `benchmarks/b0/PREREGISTRATION.md` is kept unrevised; what actually happened is
> append-only in `fleets/dsh/DECISIONS.md` (D1–D10).
>
> Its central premise died between writing and execution: the plan assumes **B0 is
> blocked and dsh unblocks it**. B0 had already run, and **FAILED** — +10.0 pp against a
> pre-registered ≥15 pp bar, n=10, with the directions disagreeing in sign. V2 is dropped.
>
> Consequently: **Phase 2 is void** (B0 ran, differently, and W3 was deliberately not used
> to re-run it for a friendlier number). **Phase 3's council was not built** — it scaled a
> pairwise result the experiment rejected. **Phase 4's WS lane was not built**; its gate
> was conditioned on a verification stack that no longer exists, and
> `loop/ws_gate.py` now refuses in code. Phases 0, 1, and the independent parts of 5 were
> implemented and verified live. Task 0.2's PyPI package name is **wrong and unsafe** —
> see D3 before running any command from it.
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire DeepSeek Harness (dsh) into Chingis as a new worker fleet and, later, as the executive's self-modification lane — unblocking B0 with a genuine second lab *now*, upgrading cross-fleet verification from a pair to a council, and giving the Rev H "model-authored harness" thesis a real substrate, all without touching the kernel's law.

**Architecture:** dsh enters as lane **W3** — a *packaged* fleet like W1/W2, but the first **glass-box** one: open-source, plugin-composed, every step in inspectable JSONL. Its `llm-pi-ai` adapter carries Anthropic (Claude), xAI (Grok), DeepSeek, and z.ai (GLM) catalogs behind one interface, so one adapter buys Chingis three new labs. Phase 4 adds lane **WS** ("sovereign"): a dsh composition with the self-referential `tool-cordis` toolset, where the executive can author its own tools as session-scoped dynamic plugins — proposals that persist **only** through the existing operator ratchet. The Python kernel (`kernel/`, `verify/`, `loop/ratchet.py`) remains the immutable law; dsh output is worker text and passes `kernel/screen.py` like every other lane's.

**Tech Stack:** existing Chingis Python 3.12/uv stack; `deepseek-harness` Python SDK (bundled JSON-RPC runtime — no Node.js needed on the host); dsh pinned at one developer-preview version; pi-ai provider catalog; SQLite (`chingis.db`) unchanged.

---

## Why this order (context for the engineer)

1. **B0 is the bottleneck for the whole M-ladder.** B0 (cross-fleet verification: do two labs' models catch each other's defects better than a model catches its own?) is blocked because W1's Codex quota is exhausted until ~2026-09-01 and a Z.ai-only run is self-review. dsh + `ANTHROPIC_API_KEY` (or `XAI_API_KEY` / `DEEPSEEK_API_KEY`) is a metered second lab **today** — no subscription, no quota window.
2. **B0's original design varied only the lab** (W1 and W2 are both Claude Code shells). A W2×W3 contrast varies lab *and* shell — a confound. This plan turns the confound into better science: W3 can also serve GLM, so a **2×2 (lab × shell)** run separates the two effects. Whether to run minimal (W2×W3-claude) or the 2×2 is an operator decision recorded in `benchmarks/b0/DEVIATIONS.md` before anything runs (Task 2.1).
3. **dsh's trust stance is "treat like shell access."** Its sandbox is explicitly not a security boundary. So nothing in dsh is ever load-bearing for containment: the worktree is the blast radius (kernel-owned), worker text is screened at the event boundary (kernel-owned), persistence goes through the ratchet (operator-owned). dsh gets sovereignty *inside* the jail, never over it.

**Ground rules carried over from the repo (binding):**
- Protected paths stay protected. This plan creates new files only; it never edits `kernel/`, `verify/seeded/`, `evals/heldout/`, `docs/adaptive-harness-design.html`, or `docs/CHINGIS-implementation-plan.md`.
- Every deviation from B0's preregistration is written to `benchmarks/b0/DEVIATIONS.md` *before* the run.
- Metered spend is real USD (unlike the flat-rate fleets). Task 0.1 sets a budget cap in facts before any live call.

---

## Phase 0 — Pin, spike, decide (~1 evening)

### Task 0.1: Record the operator decisions as facts

**Files:**
- Modify: `policy/facts.md` (lane-economics table + model tiers)

**Step 1: Add the W3/WS rows to the lane-economics table in `policy/facts.md`:**

```markdown
| W3 | Metered per token (USD — real money, unlike the flat fleets) | **None — glass box.** Open-source dsh harness; session JSONL inspectable end to end | dsh JSON-RPC runtime. One lane, many labs: anthropic / xai / deepseek / zai routes via pi-ai. Budget cap: $___/day (operator-set, enforced by `budget.tokens_usd` on every W3 contract). |
| WS | Metered per token | None, and additionally self-describing: dynamic plugins logged as cordis_define/run events | dsh + tool-cordis. The executive's toolsmith lane. Session-scoped only; persistence exists solely via loop/ratchet.py. NOT WIRED until Phase 4. |
```

**Step 2: Under "Model tiers", note that Claude and Grok are now reachable as workers via W3 and record their current per-Mtok prices with a "re-verify before committing spend" date, same convention as the existing pricing note.**

**Step 3: Get the operator to fill in the daily USD cap before committing. Facts are executive-facing context — an unfilled cap is a fact ("budget: unset — do not route W3") until it isn't.**

**Step 4: Commit**

```bash
git add policy/facts.md
git commit -m "facts: add W3 (dsh glass-box fleet) and WS (sovereign lane) economics"
```

### Task 0.2: Pin dsh and prove one headless turn

**Files:**
- Create: `fleets/dsh/README.md` (what this dir is, pin policy)
- Create: `fleets/dsh/w3.cordis.yml`
- Modify: `pyproject.toml` (optional dependency group `dsh`)

**Step 1: Install the Python SDK into an optional group and PIN it — dsh is a developer preview with promised breaking changes:**

```bash
uv add --optional dsh deepseek-harness
# verify the actual PyPI name first: pip index versions deepseek-harness
# if the PyPI name differs, record the real one in fleets/dsh/README.md
```

**Step 2: Write `fleets/dsh/w3.cordis.yml`** — start from the upstream `examples/jsonrpc-agent/minimal.cordis.yml` (bash + read/write/edit + todo_write, JSONL persistence, compaction; **no** subagent, **no** tool-cordis) and add the multi-provider seam:

```yaml
# W3 worker composition. Deliberately boring: no tool-cordis, no subagents.
# The interesting lane is WS (Phase 4); W3 is a worker like any other.
- id: llm
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      anthropic:
        apiKeyEnv: ANTHROPIC_API_KEY
      xai:
        apiKeyEnv: XAI_API_KEY
      deepseek:
        apiKeyEnv: DEEPSEEK_API_KEY
      zai:
        apiKeyEnv: ZAI_API_KEY
```

**Step 3: Smoke it by hand from a scratch directory (NOT the repo):**

```python
from deepseek_harness import DeepSeekHarness
with DeepSeekHarness(provider="deepseek", model="deepseek-v4-flash",
                     cwd="/tmp/dsh-smoke", session_root="/tmp/dsh-smoke-sessions",
                     cordis="fleets/dsh/w3.cordis.yml") as h:
    r = h.run("create hello.txt containing exactly: hello from W3")
print(r.final_response)
```

Expected: `hello.txt` exists in `/tmp/dsh-smoke`, session JSONL exists under `/tmp/dsh-smoke-sessions`, nothing written anywhere else. Repeat once with `provider="anthropic"` and a small Claude model to prove the second lab authenticates. **Record both observed model IDs in `fleets/dsh/README.md`** — do not trust memory for model IDs.

**Step 4: Commit**

```bash
git add fleets/ pyproject.toml uv.lock
git commit -m "feat: pin dsh SDK, W3 worker composition, smoke verified on deepseek+anthropic"
```

---

## Phase 1 — The W3 adapter (~1–2 evenings, TDD throughout)

B2's acceptance criterion governs: **the same contract runs on any lane unmodified.**

### Task 1.1: Failing tests for the adapter contract

**Files:**
- Create: `tests/test_dsh_adapter.py`

**Step 1: Write the failing tests.** Mirror the structure of the existing lane tests (read `tests/` for the W2/glm adapter tests first and copy their fixtures — same contract fixture, same worktree fixture). The dsh-specific assertions:

```python
"""W3 = dsh fleet. Same contract, new lane. Spec §4 / B2 acceptance."""
from pathlib import Path
from adapters.base import Result, Status
from adapters.dsh_adapter import DshAdapter, SESSION_DIRNAME


def test_lane_and_result_shape(contract, worktree, fake_sdk):
    a = DshAdapter(provider="deepseek", model="deepseek-v4-flash", sdk=fake_sdk)
    r = a.run(contract, worktree)
    assert isinstance(r, Result)
    assert r.lane == "W3"
    assert r.model.startswith("deepseek/")          # lane records provider/model


def test_session_root_outside_worktree(contract, worktree, fake_sdk):
    """Session JSONL is flywheel data, not artifact. It must never enter the diff."""
    a = DshAdapter(provider="deepseek", model="deepseek-v4-flash", sdk=fake_sdk)
    a.run(contract, worktree)
    assert not (worktree / SESSION_DIRNAME).exists()
    assert fake_sdk.kwargs["session_root"].startswith(str(a.runs_dir))


def test_refusal_detected_from_final_text(contract, worktree, fake_sdk_refusing):
    a = DshAdapter(provider="anthropic", model="claude", sdk=fake_sdk_refusing)
    r = a.run(contract, worktree)
    assert r.status == Status.REFUSED
    assert r.refusal_signal


def test_sdk_crash_is_worker_failed_not_ours(contract, worktree, fake_sdk_crashing):
    a = DshAdapter(provider="deepseek", model="m", sdk=fake_sdk_crashing)
    r = a.run(contract, worktree)
    assert r.status == Status.FAILED                # normalized, never raised


def test_budget_maps_to_sdk(contract, worktree, fake_sdk):
    contract["budget"]["wall_min"] = 7
    a = DshAdapter(provider="deepseek", model="m", sdk=fake_sdk)
    a.run(contract, worktree)
    assert fake_sdk.kwargs["cwd"] == str(worktree)
```

`fake_sdk` is a fixture standing in for `DeepSeekHarness` (records kwargs, writes a canned file into cwd, returns canned `final_response`). Follow whatever doubles pattern the existing adapter tests use — if they subprocess-fake instead, do that; consistency beats novelty.

**Step 2: Run and verify they fail for the right reason:**

```bash
uv run pytest tests/test_dsh_adapter.py -q
```

Expected: `ImportError: adapters.dsh_adapter` — not fixture errors. Fix fixtures first if so.

### Task 1.2: Implement `adapters/dsh_adapter.py`

**Files:**
- Create: `adapters/dsh_adapter.py`

**Step 1: Implement.** Anchor on `glm_cc_adapter.py`'s prompt rendering (objective + context_refs + output_schema, budget→wall clamp) and `base.py`'s hygiene helpers:

```python
"""W3 — the dsh fleet. A packaged lane with zero opacity.

    contract -> prompt -> dsh JSON-RPC runtime (bash, read/write/edit in worktree)
             -> final_response + session JSONL -> Result

Same interface as every lane (B2). The dsh session log is flywheel data and is
kept under runs/, never in the worktree; anything dsh-shaped that does land in
the worktree is stripped and RECORDED, same policy as CLAUDE.md hygiene.
"""
from __future__ import annotations

import time
from pathlib import Path

from .base import Result, Status, detect_refusal, strip_tooling_artifacts

SESSION_DIRNAME = ".dsh-sessions"
CORDIS = Path(__file__).resolve().parent.parent / "fleets" / "dsh" / "w3.cordis.yml"


class DshAdapter:
    lane = "W3"

    def __init__(self, *, provider: str, model: str,
                 runs_dir: Path | None = None, sdk=None) -> None:
        self.provider, self.model = provider, model
        self.runs_dir = Path(runs_dir or Path("runs") / "w3").resolve()
        self._sdk = sdk  # test seam; None -> real DeepSeekHarness

    def run(self, contract: dict, worktree: Path) -> Result:
        prompt = self._render(contract)              # mirror glm_cc_adapter's renderer
        session_root = self.runs_dir / contract["contract_id"]
        t0 = time.monotonic()
        try:
            final = self._invoke(prompt, worktree, session_root, contract)
        except Exception as e:                        # noqa: BLE001 — worker_failed, not ours
            return Result(status=Status.FAILED, lane=self.lane,
                          model=f"{self.provider}/{self.model}",
                          artifacts={"error": str(e)[:500]})
        stripped = strip_tooling_artifacts(worktree)
        diff = self._diff(worktree)                   # same helper the other lanes use
        refusal = detect_refusal(final, diff_empty=not diff.strip())
        return Result(
            status=Status.REFUSED if refusal else Status.DONE,
            lane=self.lane, model=f"{self.provider}/{self.model}",
            refusal_signal=refusal,
            artifacts={"diff": diff, "stdout_tail": final[-2000:],
                       "stripped": stripped},
            raw_cost={"wall_s": time.monotonic() - t0},
        )
```

`_invoke` wraps `DeepSeekHarness(provider=..., model=..., cwd=str(worktree), session_root=str(session_root), cordis=str(CORDIS))` / `.run(prompt)` (or calls the injected `sdk`). `_render` and `_diff`: **extract the shared logic from `glm_cc_adapter.py` into `adapters/base.py` if it isn't already shared — DRY — rather than copying it.** Map `contract["budget"]["tokens_usd"]` to the SDK's max-tokens knob; a W3 contract with no USD budget refuses to run (metered lane, facts say budget-capped).

**Step 2: Run the tests:**

```bash
uv run pytest tests/test_dsh_adapter.py -q
```

Expected: all pass. Then the full suite — **137 pre-existing tests must stay green:**

```bash
uv run pytest -q
```

**Step 3: Commit**

```bash
git add adapters/dsh_adapter.py adapters/base.py tests/test_dsh_adapter.py
git commit -m "feat: W3 dsh adapter — glass-box packaged lane, one adapter x many labs"
```

### Task 1.3: Registration + hygiene + live proof

**Files:**
- Modify: wherever the runtime's `adapters` dict is built (find it: `grep -rn "adapters=" cli.py kernel/ --include=*.py`) — add `"W3": DshAdapter(provider=..., model=...)` with provider/model taken from the contract's `model` field when present
- Modify: `adapters/base.py` `TOOLING_ARTIFACTS` — add dsh droppings if the smoke run showed any (only with a verifiable signature, per the existing three-condition rule)

**Step 1: Register the lane; add a one-line test to the existing runtime dispatch tests that lane `"W3"` resolves.**

**Step 2: Run one LIVE contract end-to-end on the cheapest route (deepseek), V1 green:** use the same harness/scenario the B3 scenarios use. Expected: `worker output: N chars…` summary in the ledger (screen layer engaged — no raw dsh text reaches the executive), V1 pass/fail facts recorded, diff non-empty.

**Step 3: Repeat once on `anthropic`. Two labs live through one lane.**

**Step 4: Commit**

```bash
git commit -am "feat: register W3; live e2e green on deepseek and anthropic routes"
```

---

## Phase 2 — B0, unblocked (~1 weekend, mostly runtime not code)

### Task 2.1: The deviation, preregistered

**Files:**
- Modify: `benchmarks/b0/DEVIATIONS.md`

**Step 1: Write the deviation BEFORE any run, and have the operator choose the design:**

- **Option A (minimal):** second lab = Anthropic via W3. Contrast W2 (GLM, Claude Code shell) × W3-anthropic (Claude, dsh shell). Confound: lab and shell vary together. Cheapest; answers "does a second lab help" but not "why".
- **Option B (2×2, recommended):** {GLM, Claude} × {Claude Code shell (W2), dsh shell (W3)} — four cells, same contracts, same rubric. Separates lab effect from shell effect; the extra cells are the cheap ones (GLM flat-rate, deepseek-priced dsh runs). This *strengthens* the preregistered question rather than weakening it.
- Either way: W1/Codex remains the preregistered design; when its quota resets (~2026-09-01), run the original pair as confirmation. B0's verdict gates V2 exactly as before.

**Step 2: Operator signs (CHINGIS_OPERATOR=1 commit). No run before this commit exists.**

### Task 2.2: Run and score

**Step 1:** `benchmarks/b0/preflight.sh` — extend its checks to cover the W3 lane (key present, budget fact set, cordis file pinned). **Step 2:** run per `run_b0.sh` with the chosen design; artifacts under `benchmarks/b0/results/`. **Step 3:** `score_b0.py` → `results/report.md`. **Step 4:** commit results (results dir is data, not protected).

### Task 2.3: Wire V2 with a real B0 report

**Files:**
- Modify: `verify/v2_crossfleet.py` — extend `OPPOSITE` so W3 reviews and is reviewed cross-lab (`"W3": "W2"`, and route WR's review to whichever *other-lab* lane B0 validated), keeping the structural rule: **no artifact ships on self-review alone — and now, no artifact ships on same-lab review alone.**
- Test: extend the existing V2 tests: a W3-anthropic artifact must never be assigned an anthropic reviewer.

TDD as in Phase 1; `require_b0_pass()` stays the gate — if B0 FAILS, V2 stays unwired and the design says so. Commit.

---

## Phase 3 — From a pair to a council (V2.5) (~2–3 evenings)

With four labs behind W3 + W2, pairwise review can become a **council**: k reviewers of distinct lab lineage score against the same B0 rubric; quorum decides.

**Files:**
- Create: `verify/v25_council.py`
- Create: `tests/test_v25_council.py`

Design constraints (write these as the module docstring):
- Reviewer set is chosen by **lineage diversity**, never by the executive (the executive picking its own judges is the fox auditing the henhouse — reviewer assignment is kernel-side, seeded, logged).
- Quorum rule preregistered before first use (e.g. 2-of-3 distinct labs; ties escalate to operator), stored in `policy/` as a fact with provenance, adjustable only via the ratchet.
- Cost shape: council only for artifacts crossing a stakes threshold (protected-adjacent diffs, promotion candidates); pairwise V2 remains the default. The executive may *request* council; the kernel decides.
- Calibration: council verdicts on `verify/seeded/` defects (read-only) are scored against the B0 floor every cycle — a council that underperforms pairwise V2 gets demoted by fact, not by feeling.

TDD; commit per module.

---

## Phase 4 — WS: the sovereign lane (~2 weekends; opens only after B0 verdict + one clean V2/V2.5 cycle)

This is the Rev H thesis made concrete: the executive gets a lane where **the harness itself is model-authored** — dsh with `cordis-host-runner` + `tool-cordis`, so it can define, run, and dispose *dynamic plugins* (new tools, new policies) inside its own session.

**Files:**
- Create: `fleets/dsh/ws.cordis.yml` — W3 composition + Code Mode + cordis toolset (mirror upstream `examples/headless-agent/advanced.cordis.yml`)
- Create: `adapters/ws_adapter.py` — subclass/extend `DshAdapter`; lane `"WS"`; additionally harvests `cordis_define` records from the session JSONL after the run
- Create: `loop/plugin_proposal.py` — serializes a harvested dynamic plugin (name, purpose, host/browser halves, its in-session eval outcomes) into `loop/proposals/staging.yaml`
- Test: `tests/test_ws_adapter.py`, `tests/test_plugin_proposal.py`

The five load-bearing rules (encode each as a test):
1. **Session-scoped by construction.** dsh dynamic packages die with the process; the adapter asserts the composition writes no plugin files, touches no `cordis.yml`. A WS run that leaves persistent state is a FAILED result and an incident event.
2. **Proposals, never adoptions.** The only exit from WS is `loop/proposals/staging.yaml`. `loop/ratchet.py` is not modified — the operator adopts, and an adopted plugin lands in `fleets/dsh/plugins/` and joins `w3.cordis.yml`/`ws.cordis.yml` by explicit include. The loop may propose; only the operator may adopt. (Unchanged §10.)
3. **Screened like any worker.** WS final text, plugin purposes, and eval claims pass `kernel/screen.py`. A dynamic plugin's self-description is UNTRUSTED content — the executive that *requested* the tool never reads the tool's own prose about itself raw; it reads the kernel's shape summary plus V1 facts from exercising it.
4. **Toolsmith contracts are still contracts.** New contract flavor in `contracts/` docs (not schema-breaking: `objective` = "build a tool that…", `verification` = the eval scenarios the tool must win on, in the worktree). Same budget, same V-stack.
5. **A proposed tool is verified by a council it didn't meet.** Promotion candidates from WS get V2.5 review by labs other than the one that authored the plugin — self-improvement bounded by verifier lineage diversity, which is the Rev G "bound at the verifier" relocated to the toolsmith loop.

Also in this phase: **reflex bridge** — B4 reflexes may now carry dsh *settings patches* (route/model/reasoning-effort choices for W3/WS) as reflex payloads, TTL'd and provenance-stamped exactly like existing reflexes. The executive tunes its own fleet's knobs; the kernel logs every patch; expiry reverts.

---

## Phase 5 — M-ladder arms, kill criteria, and what "the goal is AGI" means here

**Files:**
- Modify: `benchmarks/m0_control.py` — add two arms: `dsh-fixed` (W3, stock composition, no executive adaptivity) and `dsh-sovereign` (WS with toolsmith contracts allowed)
- Create: `evals/scenarios/` additions for toolsmith tasks (tasks where the *right move* is to build a tool first)
- Modify: `loop/score.py` consumers only as needed to attribute wins per arm

**The measurement:** the ladder's question becomes falsifiable per arm — does {live executive} beat {fixed harness}; does {executive + self-authored tools} beat {live executive}; does the gap *compound* across consolidation cycles under an unchanged verifier bound? Week-over-week, same held-out rotation discipline (`ops/rotate_heldout.py`), no human-authored capability changes inside a measurement window.

**Kill criteria (additive to the existing ones; wire as kernel-side checks, log as `POLICY_EXCEPTION`, halt the loop on any):**
- Any WS/W3 process writes outside its worktree + runs dir → halt lane.
- Any proposal whose diff touches protected paths → auto-reject at staging, incident, halt loop pending operator review (an executive that *keeps proposing* gate changes is itself a signal — count and surface it).
- Council catch-rate on seeded defects drops below the B0 floor → V-stack is degrading → freeze promotions.
- Three consecutive consolidation cycles with negative held-out delta → stop, operator review, no "one more cycle".

**Honesty clause (put this verbatim in the README status table when Phase 5 lands):** this platform does not produce AGI; it produces *evidence* — falsifiable, preregistered, deviation-logged evidence — about whether verifier-bounded self-improvement compounds. The direction is the goal; the ladder is the instrument; the ratchet is why the operator stays sovereign over every step that persists. Capability scales by adding labs and lanes; the bound never moves except by the operator's hand.

---

## Not building (scope fence)

- No dsh Web UI in production paths (dev convenience only, localhost).
- No E2B/cloud sandboxes — the worktree is the blast radius, as today.
- No dsh subagents in W3 (opacity creep); WS may propose subagent use, ratchet decides.
- No unpinned dsh upgrades; version bumps are proposals with a changelog read, like any other.
- No OAuth/subscription plumbing through dsh — metered keys only; the flat-rate fleets already cover subscription economics.

## Execution order and gates

```
Phase 0 ──► Phase 1 ──► Phase 2 (B0!) ──► Phase 3 ──► Phase 4 ──► Phase 5
   │            │            │
 operator     137+new      deviation signed BEFORE run;
 budget cap   all green    B0 verdict gates V2 wiring exactly as today
```

Phases 0–2 are worth doing this week — they unblock B0 roughly two weeks before the Codex quota resets, and the confirmation run against the original W1 design comes free in September.
