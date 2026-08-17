# W3 — the dsh fleet

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) as a Chingis
worker lane, driven headless as a subprocess exactly the way W1 and W2 drive Claude Code.

## Why this lane exists

B0 returned **FAIL** on 2026-08-17: cross-fleet review beat self-review by +10.0 pp
against a pre-registered ≥15 pp bar, with the two directions disagreeing in sign. Its
report states the consequence plainly — *"The dual-fleet topology must now be
re-justified on routing grounds alone — quota arbitrage and refusal rerouting."*

W3 is that justification, and nothing more:

| Ground | What W3 gives |
|---|---|
| **Quota arbitrage** | W2 spends flat-rate GLM coding-plan quota. W3 reaches the same lab at the same endpoint, **metered**. When quota is scarce and dollars are not, that is a different lane at identical model weights. |
| **Refusal rerouting** | One adapter reaches four labs — `zai`, `anthropic`, `xai`, `deepseek` — by changing one string. A refusal has somewhere to go without a new adapter, CLI, or subscription. |
| **No subscription** | W1's Codex allowance is exhausted until ~2026-09-01. W3 needs an API key. |

**W3 is not a verification lane.** V2 is dropped; `verify/v2_crossfleet.py` still refuses
to wire, and adding a third fleet does not revive a hypothesis the experiment rejected.

## Honesty note — "glass box" is a property of the SOURCE, not of this run

dsh is open source and persists sessions as zstd-compressed JSONL. It is tempting to
record W3 as a transparent lane. **Measured, it is not.** In the stock headless
composition the session file carries the session header only:

```json
{"type":"session","version":0,"id":"session-…","createdAt":…,"cwd":"…","delegationDepth":0}
```

No per-step tool calls, no messages. So W3 is *auditable in principle* and *opaque in
practice today*, and it is treated exactly as W1/W2 are: judged by its diff, its prose
screened at the event boundary. Making the inner loop observable is real work that has
not been done. Do not let "the harness is open source" get written down as evidence that
a run was observed.

## Runtime pins

| Thing | Pin | Why |
|---|---|---|
| `@deepseek-ai/dsh` | `0.1.0-rc.6` | Upstream is a **developer preview** and says breaking changes will happen. `package.json` + `package-lock.json` are tracked; `node_modules/` is not. |
| Node | `v24.19.0` LTS, vendored under `.runtime/` | dsh imports `createZstdDecompress`, added in Node 23.8/22.15. This machine's brew node is **23.7.0**, which fails at plugin load. The lane vendors its own runtime rather than mutating the operator's toolchain; `dsh_adapter.py` prepends it to `PATH`. |

Reinstall both:

```sh
cd fleets/dsh && npm ci
mkdir -p .runtime && cd .runtime \
  && curl -sL https://nodejs.org/dist/v24.19.0/node-v24.19.0-darwin-arm64.tar.xz | tar x
```

## Composition

`w3.cordis.yml` is a **patch overlay** on dsh's stock `headless` profile, not a whole
tree. It sets four things and deliberately leaves the rest alone:

1. **`llm-pi-ai` routes** — the four labs, each as an `apiKeyEnv` *reference*. No secret
   is in this file. An unset key fails at request time in dsh, so
   `adapters/dsh_adapter.py` refuses such a route up front instead — a configuration
   error should not arrive wearing a worker's clothes.
2. **`agent-default-model`** — `zai/glm-5.2` for a bare run; the adapter overrides per
   contract via the contract's `model` field (`"provider/model"`).
3. **`approval: never` + a named `workspace-write-unattended` preset** — headless has no
   UI to answer a prompt, so the stock `ask` policy would stall the lane until its
   wall-clock budget killed it. This removes the *prompt*, not the containment: the
   sandbox stays `workspace-write`, rooted at the process cwd, which the adapter sets to
   the worktree. dsh refuses to boot on an unnamed permission posture, which is a good
   refusal — hence the explicit preset rather than reaching for `danger-full-access`,
   which would drop the sandbox entirely.
4. **`session-telemetry-otel: DISABLED`** — already the stock default; stated so an
   upstream default flip cannot silently start exporting worker sessions.

Chingis' own guarantee is unchanged and does not depend on any of this: the worktree is
the blast radius, the diff is the artifact, and `kernel/screen.py` stands between worker
text and the executive. Upstream calls its sandbox "not a security boundary"; we agree,
and do not use it as one.

## Gotcha: `.env` will clear your key

The repo's `.env` holds **empty placeholders**. `set -a; source .env` therefore *unsets*
a real `ZAI_API_KEY` inherited from the environment, and the lane then fails with dsh's
`MISSING_CREDENTIAL`. Export keys in the shell; do not source `.env` before a live run.

## Verifying the lane

```sh
uv run pytest tests/test_w3_dsh_adapter.py -q   # 12 tests, fake `dsh`, no network
uv run python cli.py health                     # W3 should read: ok (zai/glm-5.2)
uv run python tests/live_w3_smoke.py <fixture>  # real model, real money
```

The live smoke is the one that matters. Verified 2026-08-17 on `zai/glm-5.2`: real diff,
V1 `diff_scope` and `pytest` both PASS, executive shown
`worker output: 294 chars, 1 lines, 1 non-blank` with `screened_by=heuristic`, replay
chain intact. Route it elsewhere with `W3_MODEL="anthropic/claude-sonnet-4-5"` and the
matching key — changing labs is a string, which is the entire point of the lane.

## Not here

`ws.cordis.yml` — the sovereign lane, dsh plus the self-referential `tool-cordis`
toolset, where the executive authors its own tools. Its gate is **closed**: the
integration plan opens it on a B0 verdict plus one clean cross-fleet cycle, and B0
failed. See `docs/plans/2026-08-17-dsh-integration.md` §Phase 4 and `DEVIATIONS.md`.
