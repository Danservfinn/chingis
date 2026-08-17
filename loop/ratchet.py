"""The proposal ratchet. Spec §10.

The loop may propose; only the operator may adopt. Every adoption bumps schema_version,
which costs exactly one deliberate cache re-warm -- the mechanism that makes growth
expensive enough to stay deliberate.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = Path(__file__).resolve().parent / "proposals" / "staging.yaml"
PROTECTED_LIST = ROOT / "ops" / "protected_paths.txt"
VALID_KINDS = {"action_verb", "tool", "eval_task", "skill"}


class RatchetError(ValueError):
    pass


class GateProbe(RatchetError):
    """A proposal that would edit the write-set the loop cannot reach.

    Distinct from RatchetError because it is not a malformed proposal -- it is a
    well-formed request to widen the loop's own permissions, which is an INCIDENT.
    Callers should log it and surface it, never repair and retry it.
    """


def protected_prefixes() -> tuple[str, ...]:
    """The write-set, read from the same file `ops/precommit.sh` reads.

    Deliberately not a copy: a second list is a second thing to forget, and the two
    drifting apart would mean the git hook and the ratchet disagree about what the
    loop may touch -- with the ratchet's copy being the one nobody re-reads.
    """
    if not PROTECTED_LIST.exists():
        raise RatchetError(f"the protected write-set is missing: {PROTECTED_LIST}")
    return tuple(
        line.strip() for line in PROTECTED_LIST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def touches_protected(paths: list[str]) -> list[tuple[str, str]]:
    """Return (path, matching prefix) for every path inside the write-set.

    Paths are normalized first: `./kernel/bus.py`, `kernel//bus.py`, and
    `loop/../kernel/bus.py` all name the file a raw prefix check would miss.
    """
    hits: list[tuple[str, str]] = []
    for raw in paths:
        norm = posixpath.normpath(str(raw).replace("\\", "/")).lstrip("./")
        for prefix in protected_prefixes():
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                hits.append((raw, prefix))
                break
    return hits


@dataclass
class Proposal:
    kind: str
    name: str
    rationale: str
    proposed_by: str
    status: str = "pending"
    evidence: list[str] | None = None
    operator_note: str = ""
    #: Repo-relative paths the proposal would write. Optional, so every proposal
    #: written before this field existed still loads unchanged.
    targets: list[str] | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "rationale": self.rationale,
                "evidence": self.evidence or [], "proposed_by": self.proposed_by,
                "status": self.status, "operator_note": self.operator_note,
                "targets": self.targets or []}


class Ratchet:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or STAGING)
        data = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
        self.proposals = [Proposal(**{k: v for k, v in p.items() if k in Proposal.__annotations__})
                          for p in (data or {}).get("proposals") or []]
        #: Refused attempts to write inside the protected set. Kept beside the queue
        #: rather than in a log nobody opens: one probe is noise, a pattern of them is
        #: a finding about the executive that must be visible where proposals are read.
        self.gate_probes: list[dict] = list((data or {}).get("refused_gate_probes") or [])

    def propose(self, kind: str, name: str, rationale: str, *, proposed_by: str,
                evidence: list[str] | None = None,
                targets: list[str] | None = None) -> Proposal:
        if kind not in VALID_KINDS:
            raise RatchetError(f"unknown proposal kind {kind!r}")
        if not rationale.strip():
            raise RatchetError("a proposal without a rationale is not reviewable")

        # The gate never learns. A proposal to edit the write-set is refused HERE, at
        # staging, rather than queued for an operator yes -- because a queued one is a
        # request that only has to be granted once, on a tired evening, to be permanent.
        # git's pre-commit hook is the second layer and stays the last word.
        if hits := touches_protected(targets or []):
            self.gate_probes.append({
                "name": name, "proposed_by": proposed_by, "kind": kind,
                "rationale": rationale,
                "targets": [f"{path}  (protected by: {prefix})" for path, prefix in hits],
            })
            raise GateProbe(
                f"proposal {name!r} from {proposed_by} would write inside the protected "
                f"write-set: " + "; ".join(f"{p} (protected by: {x})" for p, x in hits) +
                ". The loop may propose; it may not propose widening what it may propose. "
                "Recorded as a gate probe -- repeats are a finding about the executive, "
                "not about this proposal."
            )

        p = Proposal(kind, name, rationale, proposed_by,
                     evidence=evidence or [], targets=targets or [])
        self.proposals.append(p)
        return p

    def approve(self, name: str, *, operator: bool, note: str = "") -> Proposal:
        """Operator-only. The loop calling this is the failure this class exists to prevent."""
        if not operator:
            raise RatchetError(
                "approval is operator-only. Growth is one-way, audited, and never "
                "self-authorized -- a loop that can approve its own proposals has no ratchet."
            )
        for p in self.proposals:
            if p.name == name:
                p.status, p.operator_note = "approved", note
                return p
        raise RatchetError(f"no proposal named {name!r}")

    @property
    def pending(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "pending"]

    def save(self) -> None:
        doc = {"schema_version": 1,
               "proposals": [p.to_dict() for p in self.proposals]}
        if self.gate_probes:
            doc["refused_gate_probes"] = self.gate_probes
        self.path.write_text(yaml.safe_dump(doc, sort_keys=False))
