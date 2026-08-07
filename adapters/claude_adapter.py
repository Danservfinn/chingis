"""W1 — the Anthropic fleet. Claude Code headless against Anthropic natively.

Operator direction (2026-08-06): Claude replaces Codex as the second lab. The Codex account
is free-tier with its allowance spent until ~2026-09-01, and waiting three weeks to run the
gating experiment costs more than substituting a provider the design already sanctions --
spec §5 names Claude as a V3 verifier, so it is not a new vendor.

This is a stack change under ground rule 3, recorded here rather than left implicit.

WHY THIS IS A BETTER PAIRING THAN THE ONE SPECIFIED
---------------------------------------------------
W1 and W2 now run the SAME packaged tool (Claude Code, headless) against DIFFERENT labs:

    W1  claude -p                              -> Anthropic
    W2  claude -p + ANTHROPIC_BASE_URL=Z.ai    -> GLM

The plan's Codex-vs-GLM pairing varied two things at once: the lab and the inner shell,
each opaque in its own way. Holding the harness constant isolates the single variable B0
exists to measure -- whether two labs' models catch each other's defects better than a
model catches its own.

THE FAILURE MODE THIS FILE GUARDS AGAINST
------------------------------------------
W1 and W2 differ ONLY by environment. If ANTHROPIC_BASE_URL leaks in from the ambient
environment, .env, or a shell profile, W1 silently becomes a second W2 -- the same lab
twice -- and B0 reports "cross-fleet" while measuring self-review. That is a wrong number
that looks exactly like a right one, which is the worst kind. `_clean_env()` strips the
redirect variables and `assert_native()` fails loudly rather than producing that number.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import Result, Status, detect_refusal, strip_tooling_artifacts

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

#: Anything that could point Claude Code away from Anthropic.
REDIRECT_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

#: Sonnet, not Opus. B0 is ~60 invocations and the arms do not need frontier reasoning --
#: V3 is where max-reasoning review belongs.
DEFAULT_MODEL = "sonnet"


class FleetContamination(RuntimeError):
    """W1 was about to run against something other than Anthropic."""


def _clean_env() -> dict[str, str]:
    """Environment with every redirect variable removed. Credentials come from the
    Keychain via Claude Code's own OAuth, which is the supported path."""
    env = {k: v for k, v in os.environ.items() if k not in REDIRECT_VARS}
    return env


def assert_native() -> None:
    """Fail loudly if anything would send W1 somewhere other than Anthropic."""
    leaked = [v for v in REDIRECT_VARS if os.environ.get(v)]
    if leaked:
        raise FleetContamination(
            f"{leaked} present in the environment. W1 must reach Anthropic directly; "
            "with these set it would run against the same lab as W2 and B0 would measure "
            "self-review while reporting cross-fleet. Unset them before running."
        )


class ClaudeAdapter:
    """W1 — packaged tool, Anthropic inside."""

    lane = "W1"
    lab = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL

    def healthcheck(self) -> tuple[bool, str]:
        if not shutil.which(CLAUDE_BIN):
            return False, f"{CLAUDE_BIN} not on PATH"
        try:
            assert_native()
        except FleetContamination as e:
            return False, str(e)[:160]
        p = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True, text=True,
                           env=_clean_env())
        return p.returncode == 0, f"{p.stdout.strip()} -> anthropic ({self.model})"

    def run(self, contract: dict, worktree: Path) -> Result:
        assert_native()          # checked per dispatch, not just at startup
        started = time.time()
        wall = int(float(contract["budget"].get("wall_min", 25)) * 60)
        prompt = (f"{contract['objective']}\n\n"
                  f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
                  "Work only inside this checkout. Make the change and stop.")
        # acceptEdits, not bypassPermissions. Headless Claude Code will not write a file
        # without a permission grant -- without this the worker returns an explanation and
        # no diff, which the refusal heuristic then (correctly) reports as a refusal. The
        # grant is scoped: --add-dir plus cwd keep it inside the worktree, which is the
        # blast radius by design.
        cmd = [CLAUDE_BIN, "-p", "--output-format", "json",
               "--permission-mode", "acceptEdits",
               # A worker told to add tests will try to RUN them. Without Bash it stops
               # to ask, returns prose and an empty diff, and the refusal heuristic
               # (correctly, given what it can see) calls that a refusal.
               "--allowedTools", "Edit", "Write", "Bash", "Read", "Glob", "Grep",
               "--model", contract.get("model") or self.model,
               "--add-dir", str(worktree)]
        try:
            # Prompt on stdin: --add-dir is variadic and would swallow a positional.
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=wall,
                               env=_clean_env(), cwd=str(worktree))
        except subprocess.TimeoutExpired:
            return Result(Status.TIMEOUT, {}, {"wall_s": time.time() - started}, lane=self.lane)

        text, cost = p.stdout or "", 0.0
        try:
            d = json.loads(p.stdout)
            text = d.get("result", p.stdout)
            cost = float(d.get("total_cost_usd") or 0.0)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        stripped = strip_tooling_artifacts(worktree)
        diff = _diff(worktree)
        refusal = detect_refusal(text, diff_empty=not diff.strip())
        status = (Status.REFUSED if refusal else
                  Status.DONE if p.returncode == 0 else Status.FAILED)
        return Result(status, {"diff": diff, "summary": text[:8000],
                       "stripped_tooling_artifacts": stripped},
                      {"wall_s": round(time.time() - started, 2),
                       "usd": round(cost, 6), "quota_units_est": 1.0},
                      refusal_signal=refusal, lane=self.lane,
                      model=contract.get("model") or self.model)


def _diff(worktree: Path) -> str:
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(worktree), "diff", "--cached"],
                          capture_output=True, text=True).stdout
