"""W1 — Codex fleet. Optional packaged tool.

Two consumption modes, selected by `mode`:
  "cli"   -- `codex exec` headless, per spec §4. Flags verified against 0.146.0 at B2.
  "oauth" -- ChatGPT-plan OAuth against the Codex coding endpoint, per operator direction.

The CLI mode is the spec-faithful one ("subscriptions are agents, not endpoints").
Both share the plan's quota, so both hit the same wall when it is exhausted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import Result, Status, detect_refusal
from .codex_oauth import CodexAuth, CodexQuotaExhausted, list_models, respond

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")


class CodexAdapter:
    lane = "W1"

    def __init__(self, mode: str = "cli", model: str | None = None) -> None:
        self.mode = mode
        self.model = model

    def healthcheck(self) -> tuple[bool, str]:
        if self.mode == "cli":
            if not shutil.which(CODEX_BIN):
                return False, f"{CODEX_BIN} not on PATH"
            v = subprocess.run([CODEX_BIN, "--version"], capture_output=True, text=True)
            return v.returncode == 0, v.stdout.strip() or v.stderr.strip()
        try:
            models = list_models()
            return True, f"oauth ok, models={models}"
        except CodexQuotaExhausted as e:
            return False, f"quota exhausted (plan={e.plan_type}, resets in {e.resets_in_days:.1f}d)"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    def run(self, contract: dict, worktree: Path) -> Result:
        started = time.time()
        wall = int(float(contract["budget"].get("wall_min", 25)) * 60)
        prompt = _prompt(contract)

        if self.mode == "oauth":
            return self._run_oauth(contract, worktree, prompt, started)

        # Spec §4 / plan §6 invocation. --json --sandbox --cd all verified on 0.146.0.
        cmd = [CODEX_BIN, "exec", "--json", "--sandbox", "workspace-write",
               "--cd", str(worktree)]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(prompt)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall)
        except subprocess.TimeoutExpired:
            return Result(Status.TIMEOUT, {}, {"wall_s": time.time() - started}, lane=self.lane)

        out = (p.stdout or "") + (p.stderr or "")
        diff = _diff(worktree)
        refusal = detect_refusal(out, diff_empty=not diff.strip())
        status = (Status.REFUSED if refusal else
                  Status.DONE if p.returncode == 0 else Status.FAILED)
        return Result(status, {"diff": diff, "stdout_tail": out[-8000:]},
                      {"wall_s": round(time.time() - started, 2), "quota_units_est": 1.0},
                      refusal_signal=refusal, lane=self.lane, model=self.model or "")

    def _run_oauth(self, contract: dict, worktree: Path, prompt: str, started: float) -> Result:
        try:
            text, usage = respond(self.model or "gpt-5.6-luna",
                                  "You are a coding worker. Emit a unified diff only.", prompt)
        except CodexQuotaExhausted as e:
            # A quota wall is a routing fact, not a failure to retry into.
            return Result(Status.FAILED,
                          {"error": str(e), "resets_at": e.resets_at, "plan_type": e.plan_type},
                          {"wall_s": round(time.time() - started, 2)},
                          refusal_signal="quota_exhausted", lane=self.lane)
        except Exception as e:  # noqa: BLE001
            return Result(Status.FAILED, {"error": f"{type(e).__name__}: {e}"},
                          {"wall_s": round(time.time() - started, 2)}, lane=self.lane)
        refusal = detect_refusal(text, diff_empty=True)
        return Result(Status.REFUSED if refusal else Status.DONE,
                      {"summary": text[:8000]},
                      {"wall_s": round(time.time() - started, 2),
                       "tokens": usage.get("total_tokens", 0), "quota_units_est": 1.0},
                      refusal_signal=refusal, lane=self.lane, model=self.model or "gpt-5.6-luna")


def _prompt(contract: dict) -> str:
    return (f"{contract['objective']}\n\n"
            f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
            "Work only inside this checkout. Make the change and stop.")


def _diff(worktree: Path) -> str:
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(worktree), "diff", "--cached"],
                          capture_output=True, text=True).stdout
