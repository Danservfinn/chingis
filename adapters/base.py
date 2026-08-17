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


class AdapterError(RuntimeError):
    pass


# ------------------------------------------------------------ artifact hygiene --
# The operator's own tooling writes into the worktree. claude-mem drops CLAUDE.md files
# wherever Claude Code runs, and the packaged lanes ARE Claude Code -- so without this,
# every W1 and W2 artifact carries editor noise the reviewer then reads as part of the
# change. During B0 that is not cosmetic: it adds a spurious file to 60 diffs, and
# `src/CLAUDE.md` sits inside a `src/**` context_ref, so diff_scope would not even flag it.
#
# Isolation would be cleaner, but `--bare` and CLAUDE_CONFIG_DIR both disable the Keychain
# read that W1's OAuth depends on. So: remove at the artifact boundary, and RECORD every
# removal. A silent strip would be its own integrity problem.
TOOLING_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("CLAUDE.md", "<claude-mem-context>"),   # (filename, required signature)
    ("AGENTS.md", "<claude-mem-context>"),
)


def strip_tooling_artifacts(worktree: Path, tracked: set[str] | None = None) -> list[str]:
    """Remove tool-generated files the worker did not author. Returns what was removed.

    Three conditions must all hold, so a contract that legitimately edits a CLAUDE.md is
    untouched: the filename matches, the file was NOT in the base commit, and the content
    carries the tool's own signature.
    """
    removed: list[str] = []
    tracked = tracked if tracked is not None else _tracked_files(worktree)
    for name, signature in TOOLING_ARTIFACTS:
        for path in worktree.rglob(name):
            rel = str(path.relative_to(worktree))
            if rel in tracked:
                continue                      # pre-existing: the worker may have edited it
            try:
                if signature not in path.read_text(encoding="utf-8", errors="replace"):
                    continue                  # not this tool's output; leave it alone
            except OSError:
                continue
            path.unlink()
            removed.append(rel)
    return removed


def worktree_diff(worktree: Path) -> str:
    """The artifact every lane returns: staged diff of everything the worker touched.

    Lives here because four lanes had byte-identical copies of it, and a lane whose
    diff is computed differently from the others is not running "the same contract
    unmodified" (B2) however similar the code looks.
    """
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(worktree), "diff", "--cached"],
                          capture_output=True, text=True).stdout


def _tracked_files(worktree: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(worktree), "ls-tree", "-r", "--name-only", "HEAD"],
                         capture_output=True, text=True)
    return set(out.stdout.split())


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

        # Prune AFTER removing the directory, not before. A run killed mid-flight leaves
        # both a directory and a registration; deleting the directory first is what makes
        # the registration prunable. Getting this backwards leaves a stale entry that
        # fails every subsequent `worktree add` at this path with exit 128 -- which during
        # B0's 40 generations would look like a fleet failure rather than a local one.
        subprocess.run(["git", "-C", str(self.repo), "worktree", "prune"],
                       capture_output=True)

        p = subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", "--quiet",
             str(self.path), self.base_ref],
            capture_output=True, text=True,
        )
        if p.returncode != 0:
            raise AdapterError(
                f"could not create worktree at {self.path} from {self.repo}@{self.base_ref}: "
                f"{(p.stderr or p.stdout).strip()[:300]}"
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


def load_contract(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())
