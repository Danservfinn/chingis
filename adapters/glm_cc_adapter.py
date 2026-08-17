"""W2 — GLM fleet. Claude Code headless against the Z.ai endpoint.

The defensible way to spend coding-plan quota: an officially supported tool, driven
headless. Flat-rate, 1M-context inner model, fixed inner shell it cannot see into.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import Result, Status, detect_refusal, strip_tooling_artifacts, worktree_diff

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
DEFAULT_ENDPOINT = "https://api.z.ai/api/anthropic"


class GlmCodeAdapter:
    lane = "W2"

    def __init__(self, endpoint: str | None = None, key: str | None = None) -> None:
        self.endpoint = endpoint or os.environ.get("ZAI_ANTHROPIC_ENDPOINT") or DEFAULT_ENDPOINT
        self.key = key or os.environ.get("ZAI_KEY") or os.environ.get("ZAI_API_KEY", "")

    def healthcheck(self) -> tuple[bool, str]:
        if not shutil.which(CLAUDE_BIN):
            return False, f"{CLAUDE_BIN} not on PATH"
        if not self.key:
            return False, "ZAI_KEY / ZAI_API_KEY unset"
        return True, f"ok ({self.endpoint})"

    def run(self, contract: dict, worktree: Path) -> Result:
        started = time.time()
        wall = int(float(contract["budget"].get("wall_min", 25)) * 60)
        env = {**os.environ,
               "ANTHROPIC_BASE_URL": self.endpoint,
               "ANTHROPIC_API_KEY": self.key}
        prompt = (f"{contract['objective']}\n\n"
                  f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
                  "Work only inside this checkout. Make the change and stop.")
        # See adapters/claude_adapter.py: headless Claude Code cannot write without this.
        cmd = [CLAUDE_BIN, "-p", "--output-format", "json",
               "--permission-mode", "acceptEdits",
               # A worker told to add tests will try to RUN them. Without Bash it stops
               # to ask, returns prose and an empty diff, and the refusal heuristic
               # (correctly, given what it can see) calls that a refusal.
               "--allowedTools", "Edit", "Write", "Bash", "Read", "Glob", "Grep",
               "--add-dir", str(worktree)]
        try:
            # Prompt on stdin: --add-dir is variadic and would swallow a positional.
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=wall,
                               env=env, cwd=str(worktree))
        except subprocess.TimeoutExpired:
            return Result(Status.TIMEOUT, {}, {"wall_s": time.time() - started}, lane=self.lane)

        text = p.stdout or ""
        try:
            text = json.loads(p.stdout).get("result", p.stdout)
        except (json.JSONDecodeError, AttributeError):
            pass
        stripped = strip_tooling_artifacts(worktree)
        diff = _diff(worktree)
        refusal = detect_refusal(text, diff_empty=not diff.strip())
        status = (Status.REFUSED if refusal else
                  Status.DONE if p.returncode == 0 else Status.FAILED)
        return Result(status, {"diff": diff, "summary": text[:8000],
                       "stripped_tooling_artifacts": stripped},
                      {"wall_s": round(time.time() - started, 2), "quota_units_est": 1.0},
                      refusal_signal=refusal, lane=self.lane, model="glm-5.2")


# Kept as a module-local name so existing call sites read unchanged; the one
# implementation now lives in base.py, shared with every other lane.
_diff = worktree_diff
