"""Outcome scoring from V1 + verdicts. Spec §10, M-ladder era.

Under Rev G consolidation is outcome-driven: trajectories are scored by V1 ground truth and
cross-fleet judgment, then consolidated via advantage-filtered behaviour cloning.

Goodhart is the adversary here, not an afterthought. Cost efficiency is capped in its
contribution, and any test-weakening signal from V1 zeroes the score outright -- a
trajectory that won by deleting a failing test must never look like a good example.
"""

from __future__ import annotations

from dataclasses import dataclass

W_V1, W_V2, W_V3, W_COST = 0.5, 0.25, 0.15, 0.10


@dataclass
class Score:
    value: float
    components: dict
    disqualified: bool = False
    reason: str = ""


def score_outcome(outcome: dict, *, budget_usd: float = 1.0) -> Score:
    v1 = outcome.get("v1_detail", {}) or {}
    checks = v1.get("checks", []) or []

    weakening = [c for c in checks if c.get("check") == "diff_scope" and c.get("test_weakening")]
    if weakening:
        return Score(0.0, {"disqualified": "test_weakening"}, True,
                     "V1 detected weakened or deleted tests -- reward hacking, not a good trajectory")
    out_of_scope = [c for c in checks if c.get("check") == "diff_scope" and c.get("out_of_scope")]
    if out_of_scope:
        return Score(0.0, {"disqualified": "out_of_scope"}, True,
                     "the change touched files the contract did not name")

    v1_pass = 1.0 if outcome.get("v1_pass") else 0.0
    v2 = {"approve": 1.0, "reject": 0.0}.get(outcome.get("v2_verdict") or "", 0.5)
    v3 = {"approve": 1.0, "reject": 0.0}.get(outcome.get("v3_verdict") or "", 0.5)

    spent = float(outcome.get("cost_usd") or 0.0)
    # Capped: cheapness cannot compensate for wrongness, only break ties among correct work.
    efficiency = max(0.0, 1.0 - spent / budget_usd) if budget_usd > 0 else 0.0

    value = W_V1 * v1_pass + W_V2 * v2 + W_V3 * v3 + W_COST * efficiency
    if not v1_pass:
        value = min(value, 0.25)   # nothing that fails deterministic checks scores as good

    return Score(round(value, 4),
                 {"v1": v1_pass, "v2": v2, "v3": v3, "efficiency": round(efficiency, 3)})


def advantage_filter(scored: list[tuple[dict, Score]], *, quantile: float = 0.6) -> list[dict]:
    """Keep trajectories above the quantile. Advantage-filtered behaviour cloning."""
    live = [(d, s) for d, s in scored if not s.disqualified]
    if not live:
        return []
    values = sorted(s.value for _, s in live)
    cut = values[min(int(len(values) * quantile), len(values) - 1)]
    return [d for d, s in live if s.value >= cut]
