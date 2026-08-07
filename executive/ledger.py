"""The executive's working memory. Spec §3.

Markdown sections, hard 3k-token cap:
    GOAL · PLAN · STATE · BUDGET/QUOTA · RECENT_OUTCOMES (last 5) · OPEN_QUESTIONS

Crossing the cap forces `compress_ledger` before any other verb. Ledger bloat is a listed
failure mode -- slow, expensive, dumb decisions -- and the cap is the mitigation.

The ledger stays home. Full task state, decision history, and policy live only on the Mac;
neither lab's infrastructure ever holds the whole picture.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

TOKEN_CAP = 3000
RECENT_OUTCOMES_KEPT = 5

SECTIONS = ("GOAL", "PLAN", "STATE", "BUDGET/QUOTA", "RECENT_OUTCOMES", "OPEN_QUESTIONS")


def estimate_tokens(text: str) -> int:
    """~4 chars/token. Deliberately an estimate: the cap is a guardrail, not an accountant,
    and a tokenizer dependency here would couple the ledger to a specific model family."""
    return (len(text) + 3) // 4


@dataclass
class Ledger:
    goal: str = ""
    plan: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    budget: dict[str, object] = field(default_factory=dict)
    recent_outcomes: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ writes --
    def record_outcome(self, line: str) -> None:
        """One line each, last 5 kept. Older outcomes are the compressor's problem."""
        self.recent_outcomes.append(line.strip().replace("\n", " ")[:200])
        del self.recent_outcomes[:-RECENT_OUTCOMES_KEPT]

    def set_budget(self, snapshot: dict) -> None:
        self.budget = snapshot

    # ------------------------------------------------------------------ render --
    def render(self) -> str:
        parts = [f"## GOAL\n{self.goal or '(unset)'}"]
        parts.append("## PLAN\n" + ("\n".join(f"{i + 1}. {s}" for i, s in enumerate(self.plan))
                                    if self.plan else "(none)"))
        parts.append("## STATE\n" + ("\n".join(f"- {k}: {v}" for k, v in self.state.items())
                                     if self.state else "(empty)"))
        if self.budget:
            rows = []
            for name, m in self.budget.items():
                if isinstance(m, dict):
                    rows.append(f"- {name}: {m.get('spent')}/{m.get('limit')} ({m.get('pct')}%)")
                else:
                    rows.append(f"- {name}: {m}")
            parts.append("## BUDGET/QUOTA\n" + "\n".join(rows))
        else:
            parts.append("## BUDGET/QUOTA\n(unmetered)")
        parts.append("## RECENT_OUTCOMES\n" + ("\n".join(f"- {o}" for o in self.recent_outcomes)
                                               if self.recent_outcomes else "(none yet)"))
        parts.append("## OPEN_QUESTIONS\n" + ("\n".join(f"- {q}" for q in self.open_questions)
                                              if self.open_questions else "(none)"))
        return "\n\n".join(parts)

    # -------------------------------------------------------------------- cap --
    @property
    def tokens(self) -> int:
        return estimate_tokens(self.render())

    @property
    def over_cap(self) -> bool:
        return self.tokens > TOKEN_CAP

    def hash(self) -> str:
        """Recorded on every decision row, so a training example can be tied to the exact
        state that produced it."""
        return hashlib.sha256(self.render().encode()).hexdigest()[:16]

    def compress(self) -> str:
        """Mechanical compaction. Returns a note describing what was dropped.

        This is the kernel's floor, not the executive's `compress_ledger` verb: the model
        rewrites its own ledger with judgment, and this runs when it must be done and the
        model has not done it. Truncating silently would let the cap be violated invisibly.
        """
        before = self.tokens
        dropped: list[str] = []

        if len(self.recent_outcomes) > 3:
            dropped.append(f"{len(self.recent_outcomes) - 3} older outcomes")
            self.recent_outcomes = self.recent_outcomes[-3:]

        if len(self.plan) > 8:
            dropped.append(f"{len(self.plan) - 8} plan steps")
            self.plan = self.plan[:4] + ["… (compacted) …"] + self.plan[-3:]

        transient = [k for k in self.state if k.startswith("_")]
        if transient:
            dropped.append(f"{len(transient)} transient state keys")
            for k in transient:
                del self.state[k]

        if self.over_cap and len(self.open_questions) > 3:
            dropped.append(f"{len(self.open_questions) - 3} open questions")
            self.open_questions = self.open_questions[-3:]

        note = (f"compressed {before}->{self.tokens} tokens"
                + (f"; dropped {', '.join(dropped)}" if dropped else "; nothing droppable"))
        self.state["_last_compression"] = note
        return note
