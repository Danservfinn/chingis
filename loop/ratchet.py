"""The proposal ratchet. Spec §10.

The loop may propose; only the operator may adopt. Every adoption bumps schema_version,
which costs exactly one deliberate cache re-warm -- the mechanism that makes growth
expensive enough to stay deliberate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

STAGING = Path(__file__).resolve().parent / "proposals" / "staging.yaml"
VALID_KINDS = {"action_verb", "tool", "eval_task", "skill"}


class RatchetError(ValueError):
    pass


@dataclass
class Proposal:
    kind: str
    name: str
    rationale: str
    proposed_by: str
    status: str = "pending"
    evidence: list[str] | None = None
    operator_note: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "rationale": self.rationale,
                "evidence": self.evidence or [], "proposed_by": self.proposed_by,
                "status": self.status, "operator_note": self.operator_note}


class Ratchet:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or STAGING)
        data = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
        self.proposals = [Proposal(**{k: v for k, v in p.items() if k in Proposal.__annotations__})
                          for p in (data or {}).get("proposals") or []]

    def propose(self, kind: str, name: str, rationale: str, *, proposed_by: str,
                evidence: list[str] | None = None) -> Proposal:
        if kind not in VALID_KINDS:
            raise RatchetError(f"unknown proposal kind {kind!r}")
        if not rationale.strip():
            raise RatchetError("a proposal without a rationale is not reviewable")
        p = Proposal(kind, name, rationale, proposed_by, evidence=evidence or [])
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
        self.path.write_text(yaml.safe_dump(
            {"schema_version": 1, "proposals": [p.to_dict() for p in self.proposals]},
            sort_keys=False))
