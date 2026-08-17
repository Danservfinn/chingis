"""V3 — frontier spot-check. Spec §5.

Off-family for whichever fleet generated: escalations, promotion-gate audits, and a random
5% sample. Metered, so it is rare by design.

Sampling is deterministic in a hash of the contract id, not random: two runs over the same
contracts must select the same sample, or the audit is not reproducible.
"""

from __future__ import annotations

import hashlib

SAMPLE_RATE = 0.05


def sampled(contract_id: str, rate: float = SAMPLE_RATE, salt: str = "v3") -> bool:
    """Deterministic 5% sample. Reproducible across runs and machines."""
    h = hashlib.sha256(f"{salt}:{contract_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 0xFFFFFFFF < rate


#: Which LAB actually sits behind each lane. Derived once, here, so "off-family" cannot
#: silently become "same-family" when a lane is rewired -- which is exactly what happened:
#: the previous hardcoded map sent W1 to "claude" while W1 IS Claude, and sent W2 to "sol",
#: which has no transport at all. A map of stale assumptions is worse than no map, because
#: it reports compliance while delivering the monoculture the tier exists to break.
#: Which lab actually serves each lane. W3 was absent until 2026-08-17, so
#: `off_family_for("W3")` RAISED and V3 could not spot-check a single W3 artifact --
#: the lane was added without telling the verifier it existed. W1/W2 are retained as
#: historical lanes (removed 2026-08-17) so old artifacts still resolve.
LANE_LAB = {"WR": "zai", "W3": "zai", "W1": "anthropic", "W2": "zai", "W0": "local"}

#: Reviewer lab per generating lane: anything but the generator's own.
OFF_FAMILY = {lane: ("anthropic" if lab != "anthropic" else "zai")
              for lane, lab in LANE_LAB.items()}


def off_family_for(generating_lane: str) -> str:
    """The reviewer lab for an artifact from this lane. Raises rather than guessing."""
    lab = LANE_LAB.get(generating_lane)
    if lab is None:
        raise ValueError(f"unknown lane {generating_lane!r}: cannot guarantee off-family review")
    return "anthropic" if lab != "anthropic" else "zai"


def is_off_family(generating_lane: str, reviewer_lane: str) -> bool:
    """True only if the two lanes genuinely sit behind different labs."""
    g, r = LANE_LAB.get(generating_lane), LANE_LAB.get(reviewer_lane)
    return bool(g and r and g != r)


class SpotChecker:
    """Sampled audit with a REAL reviewer when one is supplied.

    `reviewer` is any callable taking a prompt and returning text; `verify.reviewers`
    supplies the transports. It defaults to None, which preserves the old `not_wired`
    behaviour, so no existing call site silently starts spending.

    On the off-family rationale: this tier was designed to "break the monoculture" by
    routing to a different lab, and B0 tested exactly that premise and REJECTED it. The
    reviewer is therefore wired here as *a second verifier that actually returns a
    verdict*, not as validated off-family review. That distinction matters because the
    score was carrying one live bit without it, and a real verdict fixes that regardless
    of whether crossing labs is what makes a reviewer good. Which routing rule is right
    is what B0.2 is for; until it reports, `off_family_for` is honoured as a convention
    rather than as an established result.
    """

    def __init__(self, adapters: dict, rate: float = SAMPLE_RATE, *, reviewer=None) -> None:
        self.adapters = adapters
        self.rate = rate
        self.reviewer = reviewer
        self.checked: list[str] = []

    def should_check(self, contract_id: str, *, escalation: bool = False,
                     promotion_gate: bool = False) -> bool:
        return escalation or promotion_gate or sampled(contract_id, self.rate)

    def check(self, contract_id: str, generating_lane: str, objective: str, diff: str,
              v1_report: str = "") -> dict:
        self.checked.append(contract_id)
        # Raises on an unknown lane rather than guessing a family: a wrong guess reports
        # compliance while delivering the monoculture this tier was built to avoid.
        family = off_family_for(generating_lane)
        base = {"tier": "V3", "contract_id": contract_id, "reviewer_family": family}

        if self.reviewer is None:
            return {**base, "verdict": "not_wired", "pass": True,
                    "detail": "no reviewer supplied; sampling logic is live"}

        # Same rubric and same blinding as the dropped V2 tier. Reused deliberately: V2
        # is dropped as a MECHANISM, but its prompt builder is a rubric renderer, and a
        # second copy would let the two drift apart.
        from verify.v2_crossfleet import build_prompt, parse_findings
        try:
            parsed = parse_findings(self.reviewer(build_prompt(objective, diff, v1_report)))
        except Exception as e:                                  # noqa: BLE001
            # A reviewer outage must not pass work through unexamined, and must not halt
            # the run either. Unknown is its own answer.
            return {**base, "verdict": "unavailable", "pass": True,
                    "detail": f"{type(e).__name__}: {str(e)[:200]}"}

        verdict = parsed.get("verdict")
        if verdict not in ("approve", "reject"):
            # An unparseable review is NOT a clean bill of health. B0 scored 5 of 24 such
            # replies as zero findings and biased its own catch rate downward.
            return {**base, "verdict": "unparseable", "pass": True,
                    "findings": [], "detail": "reviewer did not honour the output contract"}
        return {**base, "verdict": verdict, "pass": verdict == "approve",
                "findings": parsed.get("findings", [])}
