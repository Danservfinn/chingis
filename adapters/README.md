# adapters/ — worker lanes

**Empty by design.** Built at **B2** (Aug 17–30).

One interface, `base.py`: `run(contract) -> Result`, plus `healthcheck()`.

| File | Lane |
|---|---|
| `raw_adapter.py` | **WR** — direct model API + Chingis-owned minimal tool loop. The primary lane and the faithful path. |
| `tools/` | `bash`, `read_file`, `apply_patch` — capability-gated, worktree-scoped |
| `codex_adapter.py` | **W1** — `codex exec` headless. Optional packaged tool. |
| `glm_cc_adapter.py` | **W2** — `claude -p` → Z.ai endpoint. Optional packaged tool. |
| `local_adapter.py` | **W0** — MLX/LM Studio |

**B2 acceptance:** one contract runs on any lane unmodified; WR passes worktree-containment
tests — an adversarial contract attempting to read outside its worktree must be denied by
the capability check, **not by model politeness**.

Refusal detection is heuristic per fleet (exit codes + phrase patterns + the
empty-diff-with-explanation shape). Log every detection for tuning. A refusal is an event,
never a dead end.

Pin `codex` and `claude` versions in `.tool-versions` here and verify flags against
`--help`. Do not chase upgrades until B3.
