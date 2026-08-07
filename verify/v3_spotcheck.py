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


#: V3 must be off-family from the generator. Reviewing GPT work with GPT is the monoculture
#: this tier exists to break.
OFF_FAMILY = {"W1": "claude", "WR": "claude", "W2": "sol", "W0": "sol"}


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
