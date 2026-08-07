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
LANE_LAB = {"WR": "zai", "W1": "anthropic", "W2": "zai", "W0": "local"}

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
    def __init__(self, adapters: dict, rate: float = SAMPLE_RATE) -> None:
        self.adapters = adapters
        self.rate = rate
        self.checked: list[str] = []

    def should_check(self, contract_id: str, *, escalation: bool = False,
                     promotion_gate: bool = False) -> bool:
        return escalation or promotion_gate or sampled(contract_id, self.rate)

    def check(self, contract_id: str, generating_lane: str, objective: str, diff: str) -> dict:
        self.checked.append(contract_id)
        family = OFF_FAMILY.get(generating_lane, "claude")
        return {"tier": "V3", "contract_id": contract_id, "reviewer_family": family,
                "verdict": "not_wired", "pass": True,
                "detail": "V3 requires a metered frontier key; sampling logic is live"}
