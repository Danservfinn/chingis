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

from .base import Result, Status, detect_refusal

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
               "ANTHROPIC_AUTH_TOKEN": self.key}
        prompt = (f"{contract['objective']}\n\n"
                  f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
                  "Work only inside this checkout. Make the change and stop.")
        cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--add-dir", str(worktree), prompt]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall,
                               env=env, cwd=str(worktree))
        except subprocess.TimeoutExpired:
            return Result(Status.TIMEOUT, {}, {"wall_s": time.time() - started}, lane=self.lane)

        text = p.stdout or ""
        try:
            text = json.loads(p.stdout).get("result", p.stdout)
        except (json.JSONDecodeError, AttributeError):
            pass
        diff = _diff(worktree)
        refusal = detect_refusal(text, diff_empty=not diff.strip())
        status = (Status.REFUSED if refusal else
                  Status.DONE if p.returncode == 0 else Status.FAILED)
        return Result(status, {"diff": diff, "summary": text[:8000]},
                      {"wall_s": round(time.time() - started, 2), "quota_units_est": 1.0},
                      refusal_signal=refusal, lane=self.lane, model="glm-5.2")


def _diff(worktree: Path) -> str:
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(worktree), "diff", "--cached"],
                          capture_output=True, text=True).stdout
