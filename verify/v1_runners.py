"""V1 — deterministic verification. Kernel-run, free, always first. Spec §5.

Runs before any model sees the artifact. Tests, linters, type checks, build, schema diffs,
plus a diff-scope check the plan calls for explicitly.

V1 is the ground truth the improvement loop scores against, which makes it the thing most
worth attacking: `deleted_failing_test` is the one seeded class where V1 reports success
while the work degrades. `diff_scope` exists to catch exactly that shape.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 600


@dataclass
class V1Report:
    passed: bool
    checks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"pass": self.passed, "checks": self.checks}


def _run(cmd: str, cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-8000:]
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


#: name -> shell command. The executive names these in `verification` on the contract.
RUNNERS: dict[str, str] = {
    "pytest": "python -m pytest -q",
    "ruff": "python -m ruff check .",
    "mypy": "python -m mypy .",
    "build": "python -m build --no-isolation 2>/dev/null || true",
    "vitest": "npx vitest run",
    "eslint": "npx eslint .",
    "tsc": "npx tsc --noEmit",
}

# A change that removes or disables a test while the suite goes green is the reward-hacking
# shape the whole design fears. V1 can catch it mechanically, so it does.
TEST_WEAKENING = (
    re.compile(r"^-\s*def test_", re.M),                         # test deleted
    re.compile(r"^\+\s*@(?:pytest\.mark\.)?skip", re.M),         # test skipped
    re.compile(r"^\+\s*@pytest\.mark\.xfail", re.M),
    re.compile(r"^\+\s*(?:it|test)\.skip\(", re.M),              # js
    re.compile(r"^-\s*(?:assert|expect)\b", re.M),               # assertion removed
)


def diff_scope(diff: str, context_refs: list[str]) -> dict:
    """Did the change stay inside the paths the contract named, and did it weaken tests?"""
    touched = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    weakened = [p.pattern for p in TEST_WEAKENING if p.search(diff)]

    out_of_scope: list[str] = []
    if context_refs and not any(r.strip() in ("**", "*") for r in context_refs):
        import fnmatch
        for f in touched:
            globs = [r.replace("/**", "/*") for r in context_refs]
            if not any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(f, g + "/*")
                       or f.startswith(g.rstrip("*")) for g in globs):
                out_of_scope.append(f)

    return {"files_touched": touched, "out_of_scope": out_of_scope,
            "test_weakening": weakened,
            "pass": not out_of_scope and not weakened}


class V1Runner:
    """Executes the `verification` entries on a contract."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def run_v1(self, contract: dict, result, worktree: Path | None = None) -> dict:
        checks: list[dict] = []
        artifacts = getattr(result, "artifacts", None) or {}
        diff = artifacts.get("diff", "") or ""

        scope = diff_scope(diff, contract.get("context_refs", []))
        checks.append({"check": "diff_scope", **scope})

        wt = Path(worktree) if worktree else None
        for entry in contract.get("verification", []):
            tier, _, name = entry.partition(":")
            if tier != "V1":
                continue
            cmd = RUNNERS.get(name, name)
            if wt is None or not wt.exists():
                checks.append({"check": name, "pass": None, "detail": "worktree unavailable"})
                continue
            code, out = _run(cmd, wt, self.timeout)
            checks.append({"check": name, "pass": code == 0, "exit": code, "tail": out[-2000:]})

        decided = [c for c in checks if c.get("pass") is not None]
        return V1Report(passed=all(c["pass"] for c in decided) if decided else True,
                        checks=checks).to_dict()
