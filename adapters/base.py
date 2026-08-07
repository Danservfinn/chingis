"""One adapter interface for every lane. Spec §4.

The kernel hands an adapter a contract; the adapter returns artifacts plus a structured
result. Nothing routes by developer default -- the executive selects the lane per contract,
with each lane's economics and opacity stated to it as facts.

B2 acceptance: the same contract runs on any lane unmodified.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class Status(StrEnum):
    DONE = "done"
    FAILED = "failed"
    REFUSED = "refused"
    TIMEOUT = "timeout"


@dataclass
class Result:
    """Normalized across fleets. Spec §4."""

    status: Status
    artifacts: dict[str, Any] = field(default_factory=dict)   # diff, files, stdout_tail
    raw_cost: dict[str, float] = field(default_factory=dict)  # quota_units_est, wall_s, usd
    refusal_signal: str | None = None
    lane: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = str(self.status)
        return d


# --------------------------------------------------------------------- refusal --
# Heuristic per fleet: exit codes + phrase patterns + the empty-diff-with-explanation
# shape. Every detection is logged for tuning. A refusal is an event, never a dead end.
REFUSAL_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bI (?:can'?t|cannot|won'?t|am unable to) (?:help|assist|comply|continue|do that)",
        r"\bI'?m (?:not able|unable) to (?:help|assist|provide)",
        r"\b(?:violates|against) (?:my|our|the) (?:policy|guidelines|usage policies)",
        r"\bI must (?:decline|refuse)",
        r"\bnot something I can help with",
        r"\brefus(?:e|ing) to (?:generate|produce|write)",
    )
)


def detect_refusal(text: str, *, diff_empty: bool) -> str | None:
    """Return the matched refusal signal, or None.

    The empty-diff-with-explanation shape matters as much as the phrasing: a worker that
    produced prose instead of a patch has refused in effect, whatever it called it.
    """
    if not text:
        return None
    for pat in REFUSAL_PATTERNS:
        if m := pat.search(text):
            return m.group(0)[:120]
    if diff_empty and len(text.strip()) > 200:
        return "empty_diff_with_explanation"
    return None


# ------------------------------------------------------------------- worktrees --
class Worktree:
    """A disposable git worktree. The diff is the artifact; the worktree is the blast radius."""

    def __init__(self, repo: Path, base_ref: str, path: Path) -> None:
        # Absolute, always. `git -C <repo> worktree add <relative>` resolves the target
        # relative to the REPO, not to our cwd -- so a relative path silently creates the
        # worktree inside the target repository and hands the adapter a directory that
        # does not exist.
        self.repo, self.base_ref = Path(repo).resolve(), base_ref
        self.path = Path(path).resolve()

    def __enter__(self) -> Path:
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", "--quiet",
             str(self.path), self.base_ref],
            check=True, capture_output=True,
        )
        return self.path

    def diff(self) -> str:
        subprocess.run(["git", "-C", str(self.path), "add", "-A"], capture_output=True)
        return subprocess.run(
            ["git", "-C", str(self.path), "diff", "--cached"],
            capture_output=True, text=True,
        ).stdout

    def __exit__(self, *exc: object) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(self.path)],
            capture_output=True,
        )
        shutil.rmtree(self.path, ignore_errors=True)


# --------------------------------------------------------------------- adapter --
class Adapter(Protocol):
    lane: str

    def healthcheck(self) -> tuple[bool, str]: ...
    def run(self, contract: dict, worktree: Path) -> Result: ...


class AdapterError(RuntimeError):
    pass


def load_contract(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())
